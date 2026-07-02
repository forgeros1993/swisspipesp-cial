"""Reconcile orchestré de la projection transverse (application/projection_service).

Moule du reconcile L1 (reconciliation_service) : assemble le désiré COMPLET →
lit l'actuel → delta (cœur pur) → SHADOW par défaut ; apply=True exécute UNIQUEMENT le
delta. IDEMPOTENT (2e run = no-op strict, zéro mutation). Fake exécuteur — ZÉRO serveur.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from swisspipe.application.instanciation_service import enregistrer_modele, instancier_modele
from swisspipe.application.montage_service import archiver_montage, monter_instance
from swisspipe.application.projection_service import reconcilier_projection
from swisspipe.application.role_service import designer_titulaire_role, enregistrer_role
from swisspipe.core.domain.matrice import Matrice, NiveauPrincipal
from swisspipe.core.domain.modele import ArborescenceImposee, DossierImpose, Modele, PolitiqueDroits
from swisspipe.persistence.models import (
    Espace,
    Groupe,
    NatureEspace,
    TypeGroupe,
    signature_combinaison,
)

LECTURE = Matrice(NiveauPrincipal.LECTURE)
ECRITURE = Matrice(NiveauPrincipal.ECRITURE)
T0 = datetime(2026, 7, 1, tzinfo=UTC)


class FakeExecuteur:
    """Exécutant en mémoire : état ACL + accès base + STRUCTURE (dossiers/fichiers).

    Compte les MUTATIONS (pose/modif/retrait de règle, accès base) — les lectures ne
    comptent pas. La structure n'est JAMAIS touchée par le reconcile ACL (INV-5).
    """

    def __init__(self) -> None:
        self.etat: dict[str, dict[str, Matrice]] = {}
        self.acces_base: set[str] = set()
        self.dossiers: set[str] = {"Plans", "Correspondance"}
        self.fichiers: set[str] = {"Plans/contrat.pdf"}
        self.nb_mutations = 0

    def lire_etat(self) -> dict[str, dict[str, Matrice]]:
        return {nom: dict(par_g) for nom, par_g in self.etat.items()}

    def appliquer_delta(self, delta, groupes_desires: frozenset[str]) -> None:
        for grp in sorted(groupes_desires - self.acces_base):
            self.acces_base.add(grp)
            self.nb_mutations += 1
        for (nom, grp), m in {**delta.a_creer, **delta.a_modifier}.items():
            self.etat.setdefault(nom, {})[grp] = m
            self.nb_mutations += 1
        for nom, grp in delta.a_retirer:
            self.etat.get(nom, {}).pop(grp, None)
            if nom in self.etat and not self.etat[nom]:
                del self.etat[nom]
            self.nb_mutations += 1
        # Accès base : on ne retire QUE les groupes dont on vient de retirer des règles
        # et qui ne restent pas désirés — jamais un groupe TIERS (accès base sans règle).
        retires = {g for (_nom, g) in delta.a_retirer} - groupes_desires
        for grp in sorted(retires & self.acces_base):
            self.acces_base.discard(grp)
            self.nb_mutations += 1


def _setup(session: Session):
    modele = Modele(
        id="immobilier",
        nom="Projet immobilier",
        arborescence_imposee=ArborescenceImposee(
            dossiers=(
                DossierImpose(cle="plans", libelle="Plans"),
                DossierImpose(cle="correspondance", libelle="Correspondance"),
            ),
            dossiers_libres_autorises=False,
        ),
        roles=("responsable",),
        matrice_par_role={"responsable": {"plans": ECRITURE, "correspondance": ECRITURE}},
        politique_droits=PolitiqueDroits.IMPOSEE,
    )
    mid = enregistrer_modele(session, modele)
    inst = instancier_modele(session, modele, modele_id=mid, nom="demo", metadonnees={}, acteur="a")
    rid = enregistrer_role(session, modele_id=mid, cle="responsable", libelle="Resp")
    perso = Groupe(type=TypeGroupe.PERSONNEL, cle="zztest_grp_demo")
    session.add(perso)
    session.flush()
    designer_titulaire_role(
        session,
        instance_espace_id=inst.espace_id,
        role_id=rid,
        groupe_perso_id=perso.id,
        acteur="a",
        effectif_depuis=T0,
    )
    host = Espace(
        nature=NatureEspace.DIMENSIONNEL,
        combinaison_signature=signature_combinaison([("h", "h")]),
    )
    session.add(host)
    session.flush()
    montage = monter_instance(
        session,
        espace_transverse_id=inst.espace_id,
        espace_hote_id=host.id,
        chemin_hote="zztest_transverse_demo",
        portee_chemins={"/Plans", "/Correspondance"},
        matrice_plafond={"/Plans": ECRITURE, "/Correspondance": LECTURE},
        consenti_par="a",
        acteur="a",
    )
    return montage.montage_id


def test_shadow_par_defaut_zero_mutation(db_session: Session) -> None:
    montage_id = _setup(db_session)
    fake = FakeExecuteur()

    rapport = reconcilier_projection(db_session, montage_id, executeur=fake)  # apply=False

    assert not rapport.applique
    assert set(rapport.delta.a_creer) == {
        ("Plans", "zztest_grp_demo"),
        ("Correspondance", "zztest_grp_demo"),
    }
    assert fake.nb_mutations == 0  # shadow : on regarde, on ne touche pas (T2)


def test_premier_apply_pose_tout(db_session: Session) -> None:
    montage_id = _setup(db_session)
    fake = FakeExecuteur()

    rapport = reconcilier_projection(db_session, montage_id, executeur=fake, apply=True)

    assert rapport.applique
    assert fake.etat == {
        "Plans": {"zztest_grp_demo": ECRITURE},
        "Correspondance": {"zztest_grp_demo": LECTURE},  # plafond LECTURE appliqué
    }
    assert fake.acces_base == {"zztest_grp_demo"}


def test_second_apply_noop_strict(db_session: Session) -> None:
    montage_id = _setup(db_session)
    fake = FakeExecuteur()
    reconcilier_projection(db_session, montage_id, executeur=fake, apply=True)
    fake.nb_mutations = 0  # reset après la pose

    rapport = reconcilier_projection(db_session, montage_id, executeur=fake, apply=True)

    assert rapport.delta.est_vide
    assert not rapport.applique  # no-op strict (moule L1)
    assert fake.nb_mutations == 0  # ZÉRO mutation


def test_derive_ramenee_au_desire(db_session: Session) -> None:
    montage_id = _setup(db_session)
    fake = FakeExecuteur()
    reconcilier_projection(db_session, montage_id, executeur=fake, apply=True)

    # DÉRIVE : Correspondance repassée ÉCRITURE côté "serveur".
    fake.etat["Correspondance"]["zztest_grp_demo"] = ECRITURE

    rapport = reconcilier_projection(db_session, montage_id, executeur=fake, apply=True)

    assert rapport.delta.a_modifier == {("Correspondance", "zztest_grp_demo"): LECTURE}
    assert fake.etat["Correspondance"]["zztest_grp_demo"] == LECTURE  # ramenée au désiré


class FakeExecuteurFidele(FakeExecuteur):
    """Fake FIDÈLE au serveur : stocke ce que la relecture ACL RENDRAIT réellement.

    Simule le round-trip pose→relecture : verbes ACL (matrice_vers_verbes_acl) →
    bits → regle_acl_vers_matrice. Les additionnels non traduisibles (CLASSEMENT,
    TÉLÉCHARGEMENT) sont donc PERDUS à la relecture — comme sur le vrai Nextcloud.
    """

    def appliquer_delta(self, delta, groupes_desires: frozenset[str]) -> None:
        from swisspipe.adapters.outbound.nextcloud.traduction import (
            matrice_vers_verbes_acl,
            regle_acl_vers_matrice,
        )

        bits = {"read": 1, "write": 2, "create": 4, "delete": 8, "share": 16}
        releus = {}
        for cle, m in {**delta.a_creer, **delta.a_modifier}.items():
            perms = sum(bits[v[1:]] for v in matrice_vers_verbes_acl(m) if v.startswith("+"))
            releus[cle] = regle_acl_vers_matrice(31, perms)
        delta_fidele = type(delta)(
            a_creer={k: releus[k] for k in delta.a_creer},
            a_modifier={k: releus[k] for k in delta.a_modifier},
            a_retirer=delta.a_retirer,
        )
        super().appliquer_delta(delta_fidele, groupes_desires)


def test_idempotence_avec_additionnel_non_projetable(db_session: Session) -> None:
    """CLASSEMENT n'a pas de verbe ACL : sans normalisation, la relecture diverge du
    désiré et le reconcile n'est JAMAIS vide (a_modifier perpétuel). Le reconcile doit
    comparer le PROJETABLE (comme le documente permissions_nextcloud_vers_matrice)."""
    from swisspipe.core.domain.matrice import DroitAdditionnel

    ecriture_classement = Matrice(NiveauPrincipal.ECRITURE, {DroitAdditionnel.CLASSEMENT})
    modele = Modele(
        id="immobilier",
        nom="Projet immobilier",
        arborescence_imposee=ArborescenceImposee(
            dossiers=(DossierImpose(cle="plans", libelle="Plans"),),
            dossiers_libres_autorises=False,
        ),
        roles=("responsable",),
        matrice_par_role={"responsable": {"plans": ecriture_classement}},
        politique_droits=PolitiqueDroits.IMPOSEE,
    )
    mid = enregistrer_modele(db_session, modele)
    inst = instancier_modele(
        db_session, modele, modele_id=mid, nom="demo", metadonnees={}, acteur="a"
    )
    rid = enregistrer_role(db_session, modele_id=mid, cle="responsable", libelle="Resp")
    perso = Groupe(type=TypeGroupe.PERSONNEL, cle="zztest_grp_demo")
    db_session.add(perso)
    db_session.flush()
    designer_titulaire_role(
        db_session,
        instance_espace_id=inst.espace_id,
        role_id=rid,
        groupe_perso_id=perso.id,
        acteur="a",
        effectif_depuis=T0,
    )
    host = Espace(
        nature=NatureEspace.DIMENSIONNEL,
        combinaison_signature=signature_combinaison([("h", "h")]),
    )
    db_session.add(host)
    db_session.flush()
    montage = monter_instance(
        db_session,
        espace_transverse_id=inst.espace_id,
        espace_hote_id=host.id,
        chemin_hote="zztest_transverse_demo",
        portee_chemins={"/Plans"},
        matrice_plafond={"/Plans": ecriture_classement},  # plafond conserve CLASSEMENT
        consenti_par="a",
        acteur="a",
    )

    fake = FakeExecuteurFidele()
    reconcilier_projection(db_session, montage.montage_id, executeur=fake, apply=True)
    fake.nb_mutations = 0

    rapport = reconcilier_projection(db_session, montage.montage_id, executeur=fake, apply=True)

    assert rapport.delta.est_vide  # PAS d'a_modifier perpétuel
    assert fake.nb_mutations == 0


def test_groupe_tiers_acces_base_intact(db_session: Session) -> None:
    """Le balayage d'accès base ne touche QUE les groupes dont on a retiré des règles —
    un groupe TIERS (accès base sans règle gérée) n'est JAMAIS déconnecté."""
    montage_id = _setup(db_session)
    fake = FakeExecuteur()
    fake.acces_base.add("groupe_tiers_admin")  # présent AVANT nous, aucune règle ACL

    reconcilier_projection(db_session, montage_id, executeur=fake, apply=True)
    assert "groupe_tiers_admin" in fake.acces_base  # intact après la pose

    archiver_montage(db_session, montage_id, acteur="a")
    reconcilier_projection(db_session, montage_id, executeur=fake, apply=True)
    assert "groupe_tiers_admin" in fake.acces_base  # intact même après démontage
    assert "zztest_grp_demo" not in fake.acces_base  # NOTRE groupe, lui, est retiré


def test_demontage_retire_tout_structure_intacte(db_session: Session) -> None:
    montage_id = _setup(db_session)
    fake = FakeExecuteur()
    reconcilier_projection(db_session, montage_id, executeur=fake, apply=True)
    dossiers_avant, fichiers_avant = set(fake.dossiers), set(fake.fichiers)

    archiver_montage(db_session, montage_id, acteur="a")
    rapport = reconcilier_projection(db_session, montage_id, executeur=fake, apply=True)

    assert rapport.delta.a_retirer == frozenset(
        {("Plans", "zztest_grp_demo"), ("Correspondance", "zztest_grp_demo")}
    )
    assert fake.etat == {}  # règles retirées
    assert fake.acces_base == set()  # accès base retiré
    # INV-5 : GF / sous-dossiers / fichiers INTACTS.
    assert fake.dossiers == dossiers_avant
    assert fake.fichiers == fichiers_avant
