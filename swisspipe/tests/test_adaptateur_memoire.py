"""Tests de l'adaptateur fake en mémoire (adapters/outbound/fake/adaptateur_memoire.py)."""

from __future__ import annotations

import pytest

from swisspipe.adapters.outbound.fake.adaptateur_memoire import AdaptateurMemoire
from swisspipe.core.domain.matrice import DroitAdditionnel, Matrice, NiveauPrincipal
from swisspipe.core.ports.adaptateur_ressource import (
    AdaptateurRessource,
    DescripteurRessource,
    DroitGroupe,
)

ECRITURE = Matrice(NiveauPrincipal.ECRITURE, {DroitAdditionnel.CLASSEMENT})
LECTURE = Matrice(NiveauPrincipal.LECTURE)


def _desc() -> DescripteurRessource:
    return DescripteurRessource(type="folder", chemin="/Plans", nom="Plans")


def test_creer_retourne_cle_et_memorise() -> None:
    a = AdaptateurMemoire()
    cle = a.creer_ressource(_desc())
    assert isinstance(cle, str) and cle
    assert a.est_archivee(cle) is False
    assert a.nom_courant(cle) == "Plans"
    # Deux créations -> clés distinctes.
    assert cle != a.creer_ressource(_desc())


def test_appliquer_puis_lire_rend_le_meme_etat() -> None:
    a = AdaptateurMemoire()
    cle = a.creer_ressource(_desc())
    etat = {DroitGroupe("g1", ECRITURE), DroitGroupe("g2", LECTURE)}
    a.appliquer_droits(cle, etat)
    assert a.lire_droits_effectifs(cle) == frozenset(etat)


def test_idempotence() -> None:
    a = AdaptateurMemoire()
    cle = a.creer_ressource(_desc())
    etat = [DroitGroupe("g1", ECRITURE)]
    a.appliquer_droits(cle, etat)
    premier = a.lire_droits_effectifs(cle)
    a.appliquer_droits(cle, etat)  # réapplication du même état
    assert a.lire_droits_effectifs(cle) == premier


def test_appliquer_remplace_integralement() -> None:
    a = AdaptateurMemoire()
    cle = a.creer_ressource(_desc())
    a.appliquer_droits(cle, {DroitGroupe("g1", ECRITURE)})
    a.appliquer_droits(cle, {DroitGroupe("g2", LECTURE)})  # état complet, pas un diff
    assert a.lire_droits_effectifs(cle) == frozenset({DroitGroupe("g2", LECTURE)})


def test_archiver_marque_sans_supprimer() -> None:
    a = AdaptateurMemoire()
    cle = a.creer_ressource(_desc())
    a.appliquer_droits(cle, {DroitGroupe("g1", ECRITURE)})
    a.archiver_ressource(cle)
    assert a.est_archivee(cle) is True
    # La ressource et ses droits subsistent (pas de suppression dure).
    assert a.nom_courant(cle) == "Plans"
    assert a.lire_droits_effectifs(cle) == frozenset({DroitGroupe("g1", ECRITURE)})


def test_renommer() -> None:
    a = AdaptateurMemoire()
    cle = a.creer_ressource(_desc())
    a.renommer_ressource(cle, "Plans v2")
    assert a.nom_courant(cle) == "Plans v2"


def test_cle_inconnue_leve_keyerror() -> None:
    a = AdaptateurMemoire()
    with pytest.raises(KeyError):
        a.lire_droits_effectifs("inexistante")


def test_satisfait_le_protocol() -> None:
    a = AdaptateurMemoire()
    assert isinstance(a, AdaptateurRessource)  # Protocol runtime_checkable
