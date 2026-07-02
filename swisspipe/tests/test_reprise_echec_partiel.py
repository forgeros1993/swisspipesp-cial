"""Reprise sur échec partiel (étape 10 §C) — la couture la plus sous-estimée.

Le reconcile promet la convergence par « état désiré COMPLET → delta » : un apply
interrompu au milieu laisse un état serveur PARTIEL ; le run suivant doit lire ce
partiel, calculer le delta RÉSIDUEL et CONVERGER. Prouvé sur fake, ZÉRO serveur.
Confirme aussi le transactionnel du CLI par montage (pas de demi-état committé).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from swisspipe.adapters.inbound.cli import EXIT_ERREUR, _cmd_reconcilier_transverse
from swisspipe.application.instanciation_service import enregistrer_modele, instancier_modele
from swisspipe.application.montage_service import monter_instance
from swisspipe.application.projection_service import reconcilier_projection
from swisspipe.application.role_service import designer_titulaire_role, enregistrer_role
from swisspipe.core.domain.matrice import DroitAdditionnel, Matrice, NiveauPrincipal
from swisspipe.core.domain.modele import ArborescenceImposee, DossierImpose, Modele, PolitiqueDroits
from swisspipe.persistence.models import (
    Espace,
    Groupe,
    JournalEvenement,
    NatureEspace,
    TypeGroupe,
    signature_combinaison,
)
from swisspipe.tests.test_cli_transverse import FabriqueFake, _args
from swisspipe.tests.test_reconcile_projection_service import FakeExecuteur

LECTURE = Matrice(NiveauPrincipal.LECTURE)
ECRITURE = Matrice(NiveauPrincipal.ECRITURE)
ECRITURE_CLASSEMENT = Matrice(NiveauPrincipal.ECRITURE, {DroitAdditionnel.CLASSEMENT})
T0 = datetime(2026, 7, 1, tzinfo=UTC)
GRP = "zztest_grp_demo"


class PanneSimuleeError(RuntimeError):
    """Panne réseau/SSH simulée au milieu d'un apply."""


class FakeQuiEchoue(FakeExecuteur):
    """Échoue après N mutations : laisse l'état PARTIEL (comme une coupure SSH réelle).

    Applique règle par règle (ordre déterministe) et lève à la N+1ᵉ mutation. Aucune
    donnée détruite : la structure (dossiers/fichiers) n'est jamais touchée (INV-5).
    """

    def __init__(self, echec_apres: int) -> None:
        super().__init__()
        self.echec_apres: int | None = echec_apres

    def appliquer_delta(self, delta, groupes_desires: frozenset[str]) -> None:
        for grp in sorted(groupes_desires - self.acces_base):
            self._muter(lambda g=grp: self.acces_base.add(g))
        for (nom, grp), m in sorted({**delta.a_creer, **delta.a_modifier}.items()):
            self._muter(lambda n=nom, g=grp, mm=m: self.etat.setdefault(n, {}).__setitem__(g, mm))
        for nom, grp in sorted(delta.a_retirer):
            self._muter(lambda n=nom, g=grp: self.etat.get(n, {}).pop(g, None))
        retires = {g for (_n, g) in delta.a_retirer} - groupes_desires
        for grp in sorted(retires & self.acces_base):
            self._muter(lambda g=grp: self.acces_base.discard(g))

    def _muter(self, action) -> None:
        if self.echec_apres is not None and self.nb_mutations >= self.echec_apres:
            raise PanneSimuleeError(f"panne après {self.nb_mutations} mutations")
        action()
        self.nb_mutations += 1


def _setup(session: Session, *, matrice_plans: Matrice = ECRITURE):
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
        matrice_par_role={"responsable": {"plans": matrice_plans, "correspondance": ECRITURE}},
        politique_droits=PolitiqueDroits.IMPOSEE,
    )
    mid = enregistrer_modele(session, modele)
    inst = instancier_modele(session, modele, modele_id=mid, nom="demo", metadonnees={}, acteur="a")
    rid = enregistrer_role(session, modele_id=mid, cle="responsable", libelle="Resp")
    perso = Groupe(type=TypeGroupe.PERSONNEL, cle=GRP)
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
        matrice_plafond={"/Plans": matrice_plans, "/Correspondance": LECTURE},
        consenti_par="a",
        acteur="a",
    )
    return montage.montage_id


DESIRE_FINAL = {"Plans": {GRP: ECRITURE}, "Correspondance": {GRP: LECTURE}}


def test_reprise_apres_echec_partiel_converge(db_session: Session) -> None:
    montage_id = _setup(db_session)
    # Delta initial = accès base + 2 règles = 3 mutations. Panne après 2 -> état PARTIEL.
    fake = FakeQuiEchoue(echec_apres=2)
    dossiers_avant, fichiers_avant = set(fake.dossiers), set(fake.fichiers)

    with pytest.raises(PanneSimuleeError):
        reconcilier_projection(db_session, montage_id, executeur=fake, apply=True)

    # État PARTIEL constaté : une partie posée, le reste non. Rien détruit (INV-5).
    assert 0 < fake.nb_mutations < 3
    assert fake.etat != DESIRE_FINAL
    assert fake.dossiers == dossiers_avant and fake.fichiers == fichiers_avant

    # REPRISE : la panne est levée ; le run suivant lit le PARTIEL et applique le RÉSIDUEL.
    fake.echec_apres = None
    r2 = reconcilier_projection(db_session, montage_id, executeur=fake, apply=True)
    assert not r2.delta.est_vide  # delta RÉSIDUEL (pas une re-pose complète aveugle)
    assert fake.etat == DESIRE_FINAL  # CONVERGENCE
    assert fake.acces_base == {GRP}

    # 3e run : no-op strict — l'idempotence tient APRÈS la reprise.
    fake.nb_mutations = 0
    r3 = reconcilier_projection(db_session, montage_id, executeur=fake, apply=True)
    assert r3.delta.est_vide and fake.nb_mutations == 0
    # INV-5 : rien détruit pendant l'échec ni la reprise.
    assert fake.dossiers == dossiers_avant and fake.fichiers == fichiers_avant


@pytest.mark.parametrize("echec_apres", [0, 1, 2])
def test_convergence_quelle_que_soit_la_position_de_la_panne(
    db_session: Session, echec_apres: int
) -> None:
    montage_id = _setup(db_session)
    fake = FakeQuiEchoue(echec_apres=echec_apres)
    with pytest.raises(PanneSimuleeError):
        reconcilier_projection(db_session, montage_id, executeur=fake, apply=True)
    fake.echec_apres = None
    reconcilier_projection(db_session, montage_id, executeur=fake, apply=True)
    assert fake.etat == DESIRE_FINAL  # converge depuis N'IMPORTE quel état partiel


def test_cli_transactionnel_pas_de_demi_etat_commite(db_session: Session) -> None:
    """Le CLI committe PAR MONTAGE après succès complet : un échec au milieu du delta ne
    committe RIEN de ce montage (le journal des pertes n'est pas un demi-état menteur)."""
    montage_id = _setup(db_session, matrice_plans=ECRITURE_CLASSEMENT)  # perte lossy attendue
    db_session.commit()  # en prod le montage préexiste ; on fige le setup avant le rollback CLI

    class FabriqueEchec(FabriqueFake):
        def __call__(self, mid):
            self.appels.append(mid)
            return self.par_montage.setdefault(mid, FakeQuiEchoue(echec_apres=2))

    fabrique = FabriqueEchec()
    code = _cmd_reconcilier_transverse(db_session, _args(apply=True), fabrique)

    assert code == EXIT_ERREUR  # l'échec est SIGNALÉ, pas avalé
    # Aucune ligne d'audit committée pour ce montage échoué (rollback propre) : le
    # monitoring ne voit pas une projection "partiellement réussie" mensongère.
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(JournalEvenement)
            .where(JournalEvenement.type_evenement == "projection_partielle")
        )
        == 0
    )

    # Reprise via le CLI : panne levée -> convergence + l'audit de perte est ÉCRIT.
    fabrique.par_montage[montage_id].echec_apres = None
    code2 = _cmd_reconcilier_transverse(db_session, _args(apply=True), fabrique)
    assert code2 == 0
    assert fabrique.par_montage[montage_id].etat["Correspondance"][GRP] == LECTURE
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(JournalEvenement)
            .where(JournalEvenement.type_evenement == "projection_partielle")
        )
        == 1
    )
