"""Reconcile de projection DIMENSIONNELLE (démo) — 1 GF par société, ACL par département.

Mince service réutilisant le moteur transverse (delta cœur + ExecuteurProjection) : le
désiré vient DIRECTEMENT des Octrois des Ressources de l'espace (pas de plafond/portée —
l'octroi est le désiré, déjà figé). Hermétique : Postgres local + fake, ZÉRO serveur.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from swisspipe.application.projection_service import reconcilier_projection_dimensionnelle
from swisspipe.core.domain.matrice import DroitAdditionnel, Matrice, Mode, NiveauPrincipal
from swisspipe.persistence.models import (
    Espace,
    Groupe,
    JournalEvenement,
    NatureEspace,
    Ressource,
    TypeGroupe,
    signature_combinaison,
)
from swisspipe.persistence.models import Octroi as OctroiModel
from swisspipe.tests.test_reconcile_projection_service import FakeExecuteur

LECTURE = Matrice(NiveauPrincipal.LECTURE)
ECRITURE = Matrice(NiveauPrincipal.ECRITURE)
ECRITURE_CLASSEMENT = Matrice(NiveauPrincipal.ECRITURE, {DroitAdditionnel.CLASSEMENT})


def _societe(session: Session, octrois: dict[str, dict[str, Matrice]]):
    """Espace dimensionnel « société » + ressources (départements) + octrois directs."""
    espace = Espace(
        nature=NatureEspace.DIMENSIONNEL,
        combinaison_signature=signature_combinaison(
            [("secteur", "construction"), ("societe", "alpha")]
        ),
    )
    session.add(espace)
    session.flush()
    groupes: dict[str, Groupe] = {}
    for dept, par_groupe in octrois.items():
        ressource = Ressource(type="folder", espace_id=espace.id, chemin=f"/{dept}")
        session.add(ressource)
        session.flush()
        for cle_groupe, matrice in par_groupe.items():
            grp = groupes.get(cle_groupe)
            if grp is None:
                grp = Groupe(type=TypeGroupe.ORGANISATIONNEL, cle=cle_groupe)
                session.add(grp)
                session.flush()
                groupes[cle_groupe] = grp
            session.add(
                OctroiModel(
                    ressource_id=ressource.id,
                    groupe_id=grp.id,
                    mode=Mode.MODIFIER,
                    matrice=matrice.vers_jsonb(),
                )
            )
    session.flush()
    return espace.id


def test_shadow_assemble_le_desire_depuis_les_octrois(db_session: Session) -> None:
    espace_id = _societe(
        db_session,
        {
            "Finance": {"grp_resp": ECRITURE, "grp_lecture": LECTURE},
            "Admin": {"grp_resp": ECRITURE},
        },
    )
    fake = FakeExecuteur()

    rapport = reconcilier_projection_dimensionnelle(db_session, espace_id, executeur=fake)

    assert not rapport.applique
    assert set(rapport.delta.a_creer) == {
        ("Finance", "grp_resp"),
        ("Finance", "grp_lecture"),
        ("Admin", "grp_resp"),
    }
    assert rapport.delta.a_creer[("Finance", "grp_lecture")] == LECTURE
    assert fake.nb_mutations == 0  # shadow : rien écrit


def test_apply_pose_et_idempotent(db_session: Session) -> None:
    espace_id = _societe(db_session, {"Finance": {"grp_resp": ECRITURE}})
    fake = FakeExecuteur()

    r1 = reconcilier_projection_dimensionnelle(db_session, espace_id, executeur=fake, apply=True)
    assert r1.applique
    assert fake.etat == {"Finance": {"grp_resp": ECRITURE}}
    assert fake.acces_base == {"grp_resp"}

    fake.nb_mutations = 0
    r2 = reconcilier_projection_dimensionnelle(db_session, espace_id, executeur=fake, apply=True)
    assert r2.delta.est_vide and fake.nb_mutations == 0  # idempotence


def test_derive_ramenee_et_absence_respectee(db_session: Session) -> None:
    # 'grp_lecture' n'a d'octroi QUE sur Finance : il ne doit JAMAIS apparaître sur Admin.
    espace_id = _societe(
        db_session,
        {
            "Finance": {"grp_resp": ECRITURE, "grp_lecture": LECTURE},
            "Admin": {"grp_resp": ECRITURE},
        },
    )
    fake = FakeExecuteur()
    reconcilier_projection_dimensionnelle(db_session, espace_id, executeur=fake, apply=True)

    # dérive : quelqu'un donne à grp_lecture l'écriture sur Admin (hors octrois).
    fake.etat.setdefault("Admin", {})["grp_lecture"] = ECRITURE

    rapport = reconcilier_projection_dimensionnelle(
        db_session, espace_id, executeur=fake, apply=True
    )

    assert ("Admin", "grp_lecture") in rapport.delta.a_retirer  # l'absence est un état désiré
    assert "grp_lecture" not in fake.etat.get("Admin", {})
    assert fake.etat["Finance"]["grp_lecture"] == LECTURE  # le légitime reste


def test_octroi_heriter_refuser_ignores(db_session: Session) -> None:
    # HERITER/REFUSER (matrice None) ne se projettent pas (comme le transverse).
    espace_id = _societe(db_session, {"Finance": {"grp_resp": ECRITURE}})
    ressource_id = db_session.scalar(select(Ressource.id).where(Ressource.chemin == "/Finance"))
    grp = Groupe(type=TypeGroupe.ORGANISATIONNEL, cle="grp_refuse")
    db_session.add(grp)
    db_session.flush()
    db_session.add(
        OctroiModel(ressource_id=ressource_id, groupe_id=grp.id, mode=Mode.REFUSER, matrice=None)
    )
    db_session.flush()
    fake = FakeExecuteur()
    rapport = reconcilier_projection_dimensionnelle(
        db_session, espace_id, executeur=fake, apply=True
    )
    assert ("Finance", "grp_refuse") not in rapport.delta.a_creer
    assert "grp_refuse" not in fake.etat.get("Finance", {})


def test_perte_lossy_auditee_dimensionnel(db_session: Session) -> None:
    # Symétrie avec le transverse : CLASSEMENT non projetable -> audité (INV-6), dédupliqué.
    espace_id = _societe(db_session, {"Finance": {"grp_resp": ECRITURE_CLASSEMENT}})
    fake = FakeExecuteur()

    rapport = reconcilier_projection_dimensionnelle(
        db_session, espace_id, executeur=fake, apply=True
    )

    assert len(rapport.droits_non_projetables) == 1
    assert rapport.droits_non_projetables[0].additionnels == ("classement",)
    evs = db_session.scalars(
        select(JournalEvenement).where(JournalEvenement.type_evenement == "projection_partielle")
    ).all()
    assert len(evs) == 1
    assert evs[0].espace_id == espace_id
    assert evs[0].cause["ressource"] == "Finance"

    reconcilier_projection_dimensionnelle(db_session, espace_id, executeur=fake, apply=True)
    assert (
        len(
            db_session.scalars(
                select(JournalEvenement).where(
                    JournalEvenement.type_evenement == "projection_partielle"
                )
            ).all()
        )
        == 1
    )  # pas de spam
