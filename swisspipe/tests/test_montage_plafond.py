"""Tests du plafond de montage PAR RESSOURCE (core/domain/montage.py) — spec §4.4.

Correction d'une divergence de l'étape 2 (plafond uniforme). matrice_plafond devient un
mapping {ressource → Matrice}. Règle de sûreté : le plafond doit couvrir CHAQUE ressource
de la portée (sinon montage invalide). 100% domaine pur.
"""

from __future__ import annotations

import pytest

from swisspipe.core.domain.matrice import Matrice, NiveauPrincipal
from swisspipe.core.domain.montage import (
    Portee,
    monter,
    plafond_depuis_jsonb,
    plafond_vers_jsonb,
)

LECTURE = Matrice(NiveauPrincipal.LECTURE)
ECRITURE = Matrice(NiveauPrincipal.ECRITURE)


def _monter(*, portee: set[str], plafond: dict[str, Matrice]):
    return monter(
        montage_id="m",
        espace_transverse_id="inst",
        chemins_instance={"/Plans", "/Correspondance", "/Divers"},
        espace_hote_id="hote",
        chemin_hote="/RH",
        portee=Portee(chemins=frozenset(portee)),
        matrice_plafond=plafond,
        consenti_par="admin",
    )


def test_montage_valide_plafond_par_ressource() -> None:
    m = _monter(
        portee={"/Plans", "/Correspondance"},
        plafond={"/Plans": ECRITURE, "/Correspondance": LECTURE},
    )
    assert m.matrice_plafond["/Plans"] == ECRITURE
    assert m.matrice_plafond["/Correspondance"] == LECTURE


def test_plafond_ne_couvrant_pas_toute_la_portee_refuse() -> None:
    # /Correspondance est dans la portée mais SANS entrée plafond -> refus.
    with pytest.raises(ValueError, match="plafond"):
        _monter(
            portee={"/Plans", "/Correspondance"},
            plafond={"/Plans": ECRITURE},
        )


def test_round_trip_plafond_jsonb() -> None:
    plafond = {"/Plans": ECRITURE, "/Correspondance": LECTURE}
    assert plafond_depuis_jsonb(plafond_vers_jsonb(plafond)) == plafond
