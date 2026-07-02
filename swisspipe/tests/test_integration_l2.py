"""Intégration L2 bout-en-bout (étape 10 §A) — le cycle COMPLET via les VRAIS services.

UN test qui enchaîne sans raccourci : modèle → instance → rôle → titulaire → montage →
reconcile (shadow, apply, dérive, retrait titulaire, démontage, réactivation) — avec
assertion à CHAQUE transition. Preuve niveau SYSTÈME que les briques des étapes 1-9
composent. Postgres local + fake exécuteur, ZÉRO serveur.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from swisspipe.application.instanciation_service import enregistrer_modele, instancier_modele
from swisspipe.application.montage_service import archiver_montage, monter_instance
from swisspipe.application.projection_service import reconcilier_projection
from swisspipe.application.role_service import (
    designer_titulaire_role,
    enregistrer_role,
    retirer_titulaire,
)
from swisspipe.core.domain.matrice import Matrice, NiveauPrincipal
from swisspipe.core.domain.modele import ArborescenceImposee, DossierImpose, Modele, PolitiqueDroits
from swisspipe.core.domain.montage import EtatMontage
from swisspipe.persistence.models import (
    Espace,
    Groupe,
    JournalAcces,
    Montage,
    NatureEspace,
    TypeGroupe,
    signature_combinaison,
)
from swisspipe.tests.test_reconcile_projection_service import FakeExecuteur

LECTURE = Matrice(NiveauPrincipal.LECTURE)
ECRITURE = Matrice(NiveauPrincipal.ECRITURE)
T0 = datetime(2026, 7, 1, tzinfo=UTC)
GRP = "zztest_grp_demo"


def test_cycle_l2_complet(db_session: Session) -> None:
    s = db_session

    # 1) MODÈLE (étape 1) : gabarit + matrice par rôle (étape 3).
    modele = Modele(
        id="immobilier",
        nom="Projet immobilier",
        arborescence_imposee=ArborescenceImposee(
            dossiers=(
                DossierImpose(cle="plans", libelle="Plans"),
                DossierImpose(cle="correspondance", libelle="Correspondance"),
                DossierImpose(cle="divers", libelle="Divers"),
            ),
            dossiers_libres_autorises=False,
        ),
        roles=("responsable",),
        matrice_par_role={"responsable": {"plans": ECRITURE, "correspondance": ECRITURE}},
        politique_droits=PolitiqueDroits.IMPOSEE,
    )
    mid = enregistrer_modele(s, modele)

    # 2) INSTANCE (étape 1) : squelette matérialisé.
    inst = instancier_modele(s, modele, modele_id=mid, nom="demo", metadonnees={}, acteur="a")
    espace = s.get(Espace, inst.espace_id)
    assert espace is not None and espace.nature.value == "transverse"
    assert len(inst.ressource_ids) == 3  # Plans, Correspondance, Divers

    # 3) RÔLE + TITULAIRE (étape 3) : octrois FIGÉS posés sur le groupe personnel.
    rid = enregistrer_role(s, modele_id=mid, cle="responsable", libelle="Resp")
    perso = Groupe(type=TypeGroupe.PERSONNEL, cle=GRP)
    s.add(perso)
    s.flush()
    aff = designer_titulaire_role(
        s,
        instance_espace_id=inst.espace_id,
        role_id=rid,
        groupe_perso_id=perso.id,
        acteur="a",
        effectif_depuis=T0,
    )
    assert len(aff.octroi_ids) == 2  # Plans + Correspondance
    assert (
        s.scalar(select(JournalAcces.action).where(JournalAcces.groupe_id == perso.id)) is not None
    )

    # 4) MONTAGE (étapes 2/7b) : plafond PAR RESSOURCE, portée excluant Divers.
    host = Espace(
        nature=NatureEspace.DIMENSIONNEL,
        combinaison_signature=signature_combinaison([("h", "h")]),
    )
    s.add(host)
    s.flush()
    montage = monter_instance(
        s,
        espace_transverse_id=inst.espace_id,
        espace_hote_id=host.id,
        chemin_hote="zztest_transverse_demo",
        portee_chemins={"/Plans", "/Correspondance"},
        matrice_plafond={"/Plans": ECRITURE, "/Correspondance": LECTURE},
        consenti_par="a",
        acteur="a",
    )
    mtg_id = montage.montage_id

    # 5) RECONCILE SHADOW (étapes 8/9) : delta = tout à créer, ZÉRO mutation.
    fake = FakeExecuteur()
    r_shadow = reconcilier_projection(s, mtg_id, executeur=fake)
    assert not r_shadow.applique
    assert set(r_shadow.delta.a_creer) == {("Plans", GRP), ("Correspondance", GRP)}
    assert fake.nb_mutations == 0 and fake.etat == {}

    # 6) APPLY : plafond MORD (Corr ÉCRITURE -> LECTURE), portée respectée (pas Divers).
    r_apply = reconcilier_projection(s, mtg_id, executeur=fake, apply=True)
    assert r_apply.applique
    assert fake.etat == {"Plans": {GRP: ECRITURE}, "Correspondance": {GRP: LECTURE}}
    assert "Divers" not in fake.etat
    assert fake.acces_base == {GRP}

    # 7) DÉRIVE : Correspondance repassée ÉCRITURE côté "serveur" -> ramenée au désiré.
    fake.etat["Correspondance"][GRP] = ECRITURE
    r_derive = reconcilier_projection(s, mtg_id, executeur=fake, apply=True)
    assert r_derive.delta.a_modifier == {("Correspondance", GRP): LECTURE}
    assert fake.etat["Correspondance"][GRP] == LECTURE

    # 8) RETRAIT DU TITULAIRE (étape 3) : les octrois posés par rôle sont révoqués ->
    #    le désiré perd le groupe -> le reconcile RETIRE les règles correspondantes.
    retirer_titulaire(s, aff.affectation_id, acteur="a")
    r_retrait = reconcilier_projection(s, mtg_id, executeur=fake, apply=True)
    assert r_retrait.delta.a_retirer == frozenset({("Plans", GRP), ("Correspondance", GRP)})
    assert fake.etat == {}
    assert fake.acces_base == set()

    # 8bis) RE-DÉSIGNATION (INV-3 : nouvelle affectation) -> re-pose au reconcile.
    aff2 = designer_titulaire_role(
        s,
        instance_espace_id=inst.espace_id,
        role_id=rid,
        groupe_perso_id=perso.id,
        acteur="a",
        effectif_depuis=datetime(2026, 8, 1, tzinfo=UTC),
    )
    assert aff2.affectation_id != aff.affectation_id
    reconcilier_projection(s, mtg_id, executeur=fake, apply=True)
    assert fake.etat == {"Plans": {GRP: ECRITURE}, "Correspondance": {GRP: LECTURE}}

    # 9) DÉMONTAGE (étapes 2/8) : état désiré vide -> retrait pur, structure INTACTE.
    dossiers_avant, fichiers_avant = set(fake.dossiers), set(fake.fichiers)
    archiver_montage(s, mtg_id, acteur="a")
    r_demontage = reconcilier_projection(s, mtg_id, executeur=fake, apply=True)
    assert r_demontage.delta.a_retirer == frozenset({("Plans", GRP), ("Correspondance", GRP)})
    assert fake.etat == {} and fake.acces_base == set()
    assert fake.dossiers == dossiers_avant and fake.fichiers == fichiers_avant  # INV-5

    # 10) RÉACTIVATION (réversibilité, étape 2) : la fenêtre se rouvre -> ré-exposée.
    s.get(Montage, mtg_id).etat = EtatMontage.ACTIF
    s.flush()
    r_reexpose = reconcilier_projection(s, mtg_id, executeur=fake, apply=True)
    assert set(r_reexpose.delta.a_creer) == {("Plans", GRP), ("Correspondance", GRP)}
    assert fake.etat == {"Plans": {GRP: ECRITURE}, "Correspondance": {GRP: LECTURE}}

    # 11) IDEMPOTENCE FINALE : le cycle entier se termine sur un no-op strict.
    fake.nb_mutations = 0
    r_final = reconcilier_projection(s, mtg_id, executeur=fake, apply=True)
    assert r_final.delta.est_vide and fake.nb_mutations == 0
