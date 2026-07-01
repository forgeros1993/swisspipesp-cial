"""Tests du Montage (core/domain/montage.py) — une FENÊTRE d'une instance sur un hôte.

Un montage porte OÙ (espace hôte + chemin) + le PLAFOND de droits par ressource
(RÉUTILISE la Matrice L1), JAMAIS QUI en bénéficie (INV-1). La portée (§5.5) est une
fenêtre sur une portion de l'arbo de l'instance — pas une copie. 100% domaine pur.
"""

from __future__ import annotations

import dataclasses

import pytest

from swisspipe.core.domain.matrice import Matrice, NiveauPrincipal
from swisspipe.core.domain.montage import (
    EtatMontage,
    Montage,
    Portee,
    monter,
)

LECTURE = Matrice(NiveauPrincipal.LECTURE)
ECRITURE = Matrice(NiveauPrincipal.ECRITURE)

# Arbo réelle d'une instance « Employé Alice » (3 ressources existantes).
CHEMINS_EMPLOYE = {"/Documents", "/Salaire", "/Evaluations"}


def _monter(
    *,
    portee: set[str],
    consenti_par: str = "admin_rh",
    hote: str = "espace-rh",
    chemins: set[str] | None = None,
) -> Montage:
    return monter(
        montage_id="m-1",
        espace_transverse_id="inst-employe",
        chemins_instance=CHEMINS_EMPLOYE if chemins is None else chemins,
        espace_hote_id=hote,
        chemin_hote="/Projets",
        portee=Portee(chemins=frozenset(portee)),
        matrice_plafond={c: ECRITURE for c in portee},
        consenti_par=consenti_par,
        consenti_at="2026-07-01T10:00:00Z",
    )


# ---------------------------------------------------------------------------
# §1 — Montage valide + règle des DEUX CLÉS (consentement) + INV-1
# ---------------------------------------------------------------------------


def test_montage_valide_avec_consentement() -> None:
    m = _monter(portee={"/Documents", "/Salaire"})
    assert m.espace_transverse_id == "inst-employe"
    assert m.espace_hote_id == "espace-rh"
    assert m.chemin_hote == "/Projets"
    assert m.matrice_plafond == {"/Documents": ECRITURE, "/Salaire": ECRITURE}
    assert m.consenti_par == "admin_rh"
    assert m.etat is EtatMontage.ACTIF


def test_montage_sans_consentement_refuse() -> None:
    # Règle des deux clés (INV-1) : pas de montage sans consentement de l'hôte.
    with pytest.raises(ValueError, match="consentement"):
        _monter(portee={"/Documents"}, consenti_par="")


def test_inv1_montage_ne_nomme_aucun_beneficiaire() -> None:
    champs = {f.name for f in dataclasses.fields(Montage)}
    # OÙ + PLAFOND présents.
    assert {"espace_hote_id", "chemin_hote", "matrice_plafond"} <= champs
    # QUI absent : aucun champ de bénéficiaire (groupe/personne/compte).
    interdits = {"groupe_id", "groupe", "beneficiaire", "personne", "compte_id", "membre"}
    assert champs & interdits == set()


def test_matrice_plafond_est_la_matrice_l1() -> None:
    # Pas de nouveau type de matrice : le plafond par ressource RÉUTILISE la Matrice L1.
    m = _monter(portee={"/Documents"})
    assert all(isinstance(v, Matrice) for v in m.matrice_plafond.values())


# ---------------------------------------------------------------------------
# §2 — Portée = fenêtre : expose seulement sa portion, refuse l'inexistant
# ---------------------------------------------------------------------------


def test_montage_expose_seulement_sa_portee() -> None:
    m = _monter(portee={"/Documents", "/Salaire"})
    assert m.exposees == frozenset({"/Documents", "/Salaire"})
    assert "/Evaluations" not in m.exposees


def test_portee_referencant_ressource_absente_refusee() -> None:
    with pytest.raises(ValueError, match="absente"):
        _monter(portee={"/Documents", "/Inexistant"})


def test_meme_instance_deux_portees_deux_fenetres_un_stock() -> None:
    # « Employés » monté 2 fois : RH voit tout, la personne voit un sous-ensemble.
    montage_rh = monter(
        montage_id="m-rh",
        espace_transverse_id="inst-employe",
        chemins_instance=CHEMINS_EMPLOYE,
        espace_hote_id="espace-rh",
        chemin_hote="/RH",
        portee=Portee(chemins=frozenset({"/Documents", "/Salaire", "/Evaluations"})),
        matrice_plafond={c: ECRITURE for c in ("/Documents", "/Salaire", "/Evaluations")},
        consenti_par="admin_rh",
    )
    montage_perso = monter(
        montage_id="m-perso",
        espace_transverse_id="inst-employe",
        chemins_instance=CHEMINS_EMPLOYE,
        espace_hote_id="perso:alice",
        chemin_hote="/MonEspace",
        portee=Portee(chemins=frozenset({"/Documents", "/Salaire"})),
        matrice_plafond={c: LECTURE for c in ("/Documents", "/Salaire")},
        consenti_par="alice",
    )
    # 2 fenêtres différentes...
    assert montage_rh.exposees != montage_perso.exposees
    assert montage_perso.exposees < montage_rh.exposees
    # ...sur 1 SEUL stock (même instance, mêmes ressources sous-jacentes).
    assert montage_rh.espace_transverse_id == montage_perso.espace_transverse_id
    assert montage_rh.exposees <= CHEMINS_EMPLOYE
    assert montage_perso.exposees <= CHEMINS_EMPLOYE


# ---------------------------------------------------------------------------
# §3 — Réversibilité : archiver (pas de suppression dure), réversible
# ---------------------------------------------------------------------------


def test_archiver_montage_n_expose_plus_rien() -> None:
    m = _monter(portee={"/Documents", "/Salaire"})
    archive = m.archiver()
    assert archive.etat is EtatMontage.ARCHIVE
    assert archive.exposees == frozenset()
    # La portée est conservée (archive ≠ suppression dure) -> réversible.
    assert archive.portee == m.portee


def test_archivage_est_reversible() -> None:
    m = _monter(portee={"/Documents"})
    ranime = m.archiver().reactiver()
    assert ranime.etat is EtatMontage.ACTIF
    assert ranime.exposees == frozenset({"/Documents"})


# ---------------------------------------------------------------------------
# §4 — Self-service structurel : hôte = espace PERSONNEL (structure seulement)
# ---------------------------------------------------------------------------


def test_montage_dans_espace_personnel_portee_restreinte() -> None:
    # L'hôte peut être un espace personnel, au même titre qu'un dimensionnel.
    m = _monter(portee={"/Documents"}, hote="perso:alice", consenti_par="alice")
    assert m.espace_hote_id == "perso:alice"
    assert m.exposees == frozenset({"/Documents"})
