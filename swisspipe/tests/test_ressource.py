"""Tests de l'entité Ressource (core/domain/ressource.py)."""

from __future__ import annotations

import dataclasses

import pytest

from swisspipe.core.domain.ressource import Ressource


def test_construction_ressource() -> None:
    r = Ressource("r-1", "folder", "/Plans", "esp-1")
    assert r.id == "r-1"
    assert r.type == "folder"
    assert r.chemin == "/Plans"
    assert r.espace_id == "esp-1"


def test_identite_par_id() -> None:
    # Même id, autres champs différents -> même ressource (identité = id).
    a = Ressource("r-1", "folder", "/Plans", "esp-1")
    b = Ressource("r-1", "mailbox", "/autre", "esp-9")
    assert a == b
    assert hash(a) == hash(b)


def test_ids_distincts_sont_distincts() -> None:
    a = Ressource("r-1", "folder", "/Plans", "esp-1")
    b = Ressource("r-2", "folder", "/Plans", "esp-1")
    assert a != b


def test_type_est_chaine_libre_extensible() -> None:
    # Le type n'est PAS un enum fermé : un adaptateur futur ajoute "door" sans
    # toucher au cœur.
    for t in ("folder", "mailbox", "door", "n_importe_quoi_futur"):
        assert Ressource("r", t, "/x", "esp").type == t


def test_immutabilite() -> None:
    r = Ressource("r-1", "folder", "/Plans", "esp-1")
    with pytest.raises(dataclasses.FrozenInstanceError):
        r.chemin = "/autre"  # type: ignore[misc]


def test_hachabilite() -> None:
    a = Ressource("r-1", "folder", "/Plans", "esp-1")
    b = Ressource("r-1", "folder", "/Plans", "esp-1")
    assert len({a, b}) == 1


# ---------------------------------------------------------------------------
# Invariant d'AGNOSTICITÉ (§3.3) — test qui documente et protège
# ---------------------------------------------------------------------------

# Champs autorisés, exhaustivement. La Ressource ne connaît que l'id interne et le
# type ; AUCUN identifiant externe (le mapping vit dans ressource_mapping).
CHAMPS_AUTORISES = {"id", "type", "chemin", "espace_id"}

# Marqueurs d'identifiant externe interdits dans le cœur.
MARQUEURS_EXTERNES = (
    "extern",
    "webdav",
    "nextcloud",
    "mailbox",
    "imap",
    "smtp",
    "door",
    "porte",
    "url",
    "uri",
    "mapping",
    "fileid",
    "remote",
)


def _noms_champs() -> set[str]:
    return {f.name for f in dataclasses.fields(Ressource)}


def test_aucun_champ_inattendu() -> None:
    # Échoue si quelqu'un ajoute un champ (ex. cle_externe, chemin_webdav).
    assert _noms_champs() == CHAMPS_AUTORISES


def test_aucun_marqueur_d_identifiant_externe() -> None:
    for nom in _noms_champs():
        for marqueur in MARQUEURS_EXTERNES:
            assert marqueur not in nom.lower(), (
                f"champ '{nom}' ressemble à un identifiant externe ('{marqueur}') — "
                "violation d'agnosticité §3.3 : le mapping interne↔externe vit dans "
                "ressource_mapping, jamais dans le cœur."
            )
