"""Mapping lossy AUDITABLE (étape 9 §B) — droits non projetables rendus VISIBLES.

CLASSEMENT/TÉLÉCHARGEMENT n'ont aucun verbe ACL Nextcloud (traduction.py) : ils sont
normalisés hors projection (matrice_projetable, étape 8). On NE CHANGE PAS l'enforcement —
on l'AUDITE (INV-6) : RapportProjection.droits_non_projetables + une ligne
journal_evenements 'projection_partielle' par perte lors d'un apply. journal_acces INTOUCHÉ.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine, func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from swisspipe.application.instanciation_service import enregistrer_modele, instancier_modele
from swisspipe.application.montage_service import monter_instance
from swisspipe.application.projection_service import reconcilier_projection
from swisspipe.application.role_service import designer_titulaire_role, enregistrer_role
from swisspipe.core.domain.matrice import DroitAdditionnel, Matrice, NiveauPrincipal
from swisspipe.core.domain.modele import ArborescenceImposee, DossierImpose, Modele, PolitiqueDroits
from swisspipe.persistence.models import (
    Espace,
    Groupe,
    JournalAcces,
    JournalEvenement,
    NatureEspace,
    TypeGroupe,
    signature_combinaison,
)
from swisspipe.tests.test_reconcile_projection_service import FakeExecuteur

ECRITURE = Matrice(NiveauPrincipal.ECRITURE)
ECRITURE_CLASSEMENT = Matrice(NiveauPrincipal.ECRITURE, {DroitAdditionnel.CLASSEMENT})
ECRITURE_CREATION = Matrice(NiveauPrincipal.ECRITURE, {DroitAdditionnel.CREATION})
T0 = datetime(2026, 7, 1, tzinfo=UTC)


def _setup(session: Session, matrice_plans: Matrice):
    modele = Modele(
        id="immobilier",
        nom="Projet immobilier",
        arborescence_imposee=ArborescenceImposee(
            dossiers=(DossierImpose(cle="plans", libelle="Plans"),),
            dossiers_libres_autorises=False,
        ),
        roles=("responsable",),
        matrice_par_role={"responsable": {"plans": matrice_plans}},
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
        portee_chemins={"/Plans"},
        matrice_plafond={"/Plans": matrice_plans},  # le plafond conserve l'additionnel
        consenti_par="a",
        acteur="a",
    )
    return inst, montage.montage_id


def _evenements_partiels(session: Session):
    return session.scalars(
        select(JournalEvenement).where(JournalEvenement.type_evenement == "projection_partielle")
    ).all()


# ---------------------------------------------------------------------------
# Enum étendu (migration additive) — journal_acces INTOUCHÉ
# ---------------------------------------------------------------------------


def test_enum_type_evenement_a_projection_partielle(migrated_engine: Engine) -> None:
    with migrated_engine.connect() as conn:
        labels = set(
            conn.execute(
                text(
                    "SELECT e.enumlabel FROM pg_enum e "
                    "JOIN pg_type t ON t.oid = e.enumtypid WHERE t.typname = 'type_evenement'"
                )
            ).scalars()
        )
    assert "projection_partielle" in labels


def test_enum_action_journal_toujours_inchange(migrated_engine: Engine) -> None:
    with migrated_engine.connect() as conn:
        labels = set(
            conn.execute(
                text(
                    "SELECT e.enumlabel FROM pg_enum e "
                    "JOIN pg_type t ON t.oid = e.enumtypid WHERE t.typname = 'action_journal'"
                )
            ).scalars()
        )
    assert labels == {"octroi", "revocation", "gel", "degel", "modification"}


# ---------------------------------------------------------------------------
# droits_non_projetables : liste ce qui est PERDU, pas ce qui est projetable
# ---------------------------------------------------------------------------


def test_classement_liste_comme_non_projetable(db_session: Session) -> None:
    _inst, montage_id = _setup(db_session, ECRITURE_CLASSEMENT)
    fake = FakeExecuteur()
    rapport = reconcilier_projection(db_session, montage_id, executeur=fake, apply=False)
    assert len(rapport.droits_non_projetables) == 1
    perte = rapport.droits_non_projetables[0]
    assert perte.ressource == "Plans"
    assert perte.groupe == "zztest_grp_demo"
    assert perte.additionnels == ("classement",)
    # ÉCRITURE, elle, EST projetée (le désiré normalisé la garde).
    assert rapport.delta.a_creer[("Plans", "zztest_grp_demo")].niveau.value == "ecriture"


def test_creation_pas_signalee(db_session: Session) -> None:
    # CRÉATION a un verbe ACL (+create) : projetable -> ne PAS la signaler.
    _inst, montage_id = _setup(db_session, ECRITURE_CREATION)
    fake = FakeExecuteur()
    rapport = reconcilier_projection(db_session, montage_id, executeur=fake, apply=False)
    assert rapport.droits_non_projetables == ()


def test_sans_additionnel_aucune_perte(db_session: Session) -> None:
    _inst, montage_id = _setup(db_session, ECRITURE)
    fake = FakeExecuteur()
    rapport = reconcilier_projection(db_session, montage_id, executeur=fake, apply=True)
    assert rapport.droits_non_projetables == ()
    assert _evenements_partiels(db_session) == []  # pas de perte -> 0 ligne


# ---------------------------------------------------------------------------
# Journal (INV-6) : perte AUDITÉE sur apply, jamais silencieuse
# ---------------------------------------------------------------------------


def test_apply_avec_perte_ecrit_journal_evenements(db_session: Session) -> None:
    inst, montage_id = _setup(db_session, ECRITURE_CLASSEMENT)
    acces_avant = db_session.scalar(select(func.count()).select_from(JournalAcces))
    fake = FakeExecuteur()

    reconcilier_projection(db_session, montage_id, executeur=fake, apply=True)

    evs = _evenements_partiels(db_session)
    assert len(evs) == 1
    ev = evs[0]
    assert ev.espace_id == inst.espace_id
    assert ev.cause["montage_id"] == str(montage_id)
    assert ev.cause["ressource"] == "Plans"
    assert ev.cause["groupe"] == "zztest_grp_demo"
    assert ev.cause["additionnels"] == ["classement"]
    # journal_acces INTOUCHÉ par la projection.
    assert db_session.scalar(select(func.count()).select_from(JournalAcces)) == acces_avant


def test_shadow_avec_perte_n_ecrit_pas(db_session: Session) -> None:
    # SHADOW : on constate la perte (rapport) mais on n'écrit RIEN (ni serveur ni journal).
    _inst, montage_id = _setup(db_session, ECRITURE_CLASSEMENT)
    fake = FakeExecuteur()
    rapport = reconcilier_projection(db_session, montage_id, executeur=fake, apply=False)
    assert rapport.droits_non_projetables  # constatée
    assert _evenements_partiels(db_session) == []  # pas écrite


def test_ligne_projection_partielle_append_only(db_session: Session) -> None:
    _inst, montage_id = _setup(db_session, ECRITURE_CLASSEMENT)
    reconcilier_projection(db_session, montage_id, executeur=FakeExecuteur(), apply=True)
    ev_id = _evenements_partiels(db_session)[0].id
    with pytest.raises(DBAPIError, match="append-only"):
        with db_session.begin_nested():
            db_session.execute(
                text("UPDATE journal_evenements SET acteur = 'pirate' WHERE id = :id"),
                {"id": ev_id},
            )


def test_idempotence_tient_avec_perte(db_session: Session) -> None:
    # La perte n'introduit PAS d'a_modifier perpétuel ni de spam de journal.
    _inst, montage_id = _setup(db_session, ECRITURE_CLASSEMENT)
    fake = FakeExecuteur()
    reconcilier_projection(db_session, montage_id, executeur=fake, apply=True)
    assert len(_evenements_partiels(db_session)) == 1
    fake.nb_mutations = 0

    rapport2 = reconcilier_projection(db_session, montage_id, executeur=fake, apply=True)

    assert rapport2.delta.est_vide  # idempotence étape 8 préservée
    assert fake.nb_mutations == 0
    assert len(_evenements_partiels(db_session)) == 1  # AUCUNE nouvelle ligne (pas de spam)


def test_perte_auditee_meme_si_delta_vide(db_session: Session) -> None:
    """Major revue : montage déjà conforme (projeté avant l'audit) + perte lossy ->
    la perte doit être auditée au premier apply, MÊME avec un delta vide."""
    _inst, montage_id = _setup(db_session, ECRITURE_CLASSEMENT)
    fake = FakeExecuteur()
    # État serveur DÉJÀ conforme au désiré normalisé (pré-audit) : delta sera vide.
    fake.etat = {"Plans": {"zztest_grp_demo": ECRITURE}}
    fake.acces_base = {"zztest_grp_demo"}

    rapport = reconcilier_projection(db_session, montage_id, executeur=fake, apply=True)

    assert rapport.delta.est_vide
    assert len(_evenements_partiels(db_session)) == 1  # auditée quand même (INV-6)


def test_pas_de_spam_sous_derive_persistante(db_session: Session) -> None:
    """Minor revue : une perte IDENTIQUE déjà auditée n'est PAS réécrite à chaque apply
    déclenché par une dérive sur une AUTRE ressource (dédup par existence)."""
    _inst, montage_id = _setup(db_session, ECRITURE_CLASSEMENT)
    fake = FakeExecuteur()
    reconcilier_projection(db_session, montage_id, executeur=fake, apply=True)
    assert len(_evenements_partiels(db_session)) == 1

    # Dérive externe sur la MÊME ressource (matrice changée) -> delta non vide au run 2.
    fake.etat["Plans"]["zztest_grp_demo"] = Matrice(NiveauPrincipal.LECTURE)

    rapport2 = reconcilier_projection(db_session, montage_id, executeur=fake, apply=True)

    assert not rapport2.delta.est_vide  # la dérive a bien été corrigée
    assert len(_evenements_partiels(db_session)) == 1  # perte identique PAS dupliquée
