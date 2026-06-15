"""Tests des value objects de topologie (core/domain/topologie.py)."""

from __future__ import annotations

import dataclasses

import pytest

from swisspipe.core.domain.topologie import (
    Coordonnee,
    Dimension,
    EspaceDimensionnel,
    ValeurDimension,
)

# ---------------------------------------------------------------------------
# Construction valide
# ---------------------------------------------------------------------------


def test_construction_dimension() -> None:
    d = Dimension("secteur", "Secteur", rang=0)
    assert d.cle == "secteur"
    assert d.libelle == "Secteur"
    assert d.rang == 0


def test_construction_valeur_dimension() -> None:
    v = ValeurDimension("secteur", "alpha", "Alpha")
    assert v.dimension_cle == "secteur"
    assert v.cle == "alpha"
    assert v.libelle == "Alpha"


def test_construction_coordonnee() -> None:
    c = Coordonnee("secteur", "alpha")
    assert c.dimension_cle == "secteur"
    assert c.valeur_cle == "alpha"


def test_construction_espace() -> None:
    e = EspaceDimensionnel(frozenset({Coordonnee("secteur", "alpha")}))
    assert e.coordonnees == frozenset({Coordonnee("secteur", "alpha")})


# ---------------------------------------------------------------------------
# Identité métier (cle / couple)
# ---------------------------------------------------------------------------


def test_dimension_identite_par_cle_seule() -> None:
    # Même cle, libelle/rang différents -> même dimension.
    a = Dimension("secteur", "Secteur", rang=0)
    b = Dimension("secteur", "Libellé autre", rang=9)
    assert a == b
    assert hash(a) == hash(b)


def test_valeur_dimension_identite_par_couple() -> None:
    a = ValeurDimension("secteur", "alpha", "Alpha")
    b = ValeurDimension("secteur", "alpha", "Libellé autre")
    assert a == b
    assert hash(a) == hash(b)
    # cle identique mais dimension différente -> distinct.
    assert ValeurDimension("societe", "alpha", "Alpha") != a


# ---------------------------------------------------------------------------
# Unicité de valeur par dimension dans un espace
# ---------------------------------------------------------------------------


def test_deux_valeurs_meme_dimension_rejete() -> None:
    with pytest.raises(ValueError, match="qu'une valeur dans un espace"):
        EspaceDimensionnel(
            frozenset({Coordonnee("secteur", "alpha"), Coordonnee("secteur", "finance")})
        )


def test_meme_dimension_valeurs_distinctes_via_iterable() -> None:
    # Construit depuis une liste (coercition) : doit aussi rejeter.
    with pytest.raises(ValueError, match="secteur"):
        EspaceDimensionnel([Coordonnee("secteur", "a"), Coordonnee("secteur", "b")])


def test_dimensions_distinctes_ok() -> None:
    e = EspaceDimensionnel(
        frozenset({Coordonnee("secteur", "alpha"), Coordonnee("societe", "finance")})
    )
    assert len(e.coordonnees) == 2


# ---------------------------------------------------------------------------
# Signature : déterministe, insensible à l'ordre, discriminante
# ---------------------------------------------------------------------------


def test_signature_insensible_a_l_ordre() -> None:
    coords = [Coordonnee("societe", "finance"), Coordonnee("secteur", "alpha")]
    e1 = EspaceDimensionnel(frozenset(coords))
    e2 = EspaceDimensionnel(frozenset(reversed(coords)))
    assert e1.signature == e2.signature


def test_signature_canonique_triee() -> None:
    e = EspaceDimensionnel(
        frozenset({Coordonnee("societe", "finance"), Coordonnee("secteur", "alpha")})
    )
    # Triée par dimension_cle : secteur avant societe.
    assert e.signature == "secteur=alpha;societe=finance"


def test_signature_discriminante() -> None:
    e1 = EspaceDimensionnel(frozenset({Coordonnee("secteur", "alpha")}))
    e2 = EspaceDimensionnel(frozenset({Coordonnee("secteur", "finance")}))
    assert e1.signature != e2.signature


def test_signature_meme_jeu_egale() -> None:
    jeu = frozenset({Coordonnee("secteur", "alpha"), Coordonnee("societe", "finance")})
    assert EspaceDimensionnel(jeu).signature == EspaceDimensionnel(jeu).signature


def test_espaces_meme_jeu_sont_egaux() -> None:
    jeu = {Coordonnee("secteur", "alpha")}
    assert EspaceDimensionnel(frozenset(jeu)) == EspaceDimensionnel(frozenset(jeu))


# ---------------------------------------------------------------------------
# valeur_sur
# ---------------------------------------------------------------------------


def test_valeur_sur_dimension_presente() -> None:
    e = EspaceDimensionnel(
        frozenset({Coordonnee("secteur", "alpha"), Coordonnee("societe", "finance")})
    )
    assert e.valeur_sur("secteur") == "alpha"
    assert e.valeur_sur("societe") == "finance"


def test_valeur_sur_dimension_absente() -> None:
    e = EspaceDimensionnel(frozenset({Coordonnee("secteur", "alpha")}))
    assert e.valeur_sur("departement") is None


# ---------------------------------------------------------------------------
# Immuabilité + hachabilité
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("obj", "champ", "valeur"),
    [
        (Dimension("secteur", "Secteur"), "cle", "autre"),
        (ValeurDimension("secteur", "alpha", "Alpha"), "cle", "autre"),
        (Coordonnee("secteur", "alpha"), "valeur_cle", "autre"),
        (EspaceDimensionnel(frozenset({Coordonnee("secteur", "alpha")})), "coordonnees", frozenset()),
    ],
)
def test_immutabilite(obj: object, champ: str, valeur: object) -> None:
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(obj, champ, valeur)


def test_hachabilite_dans_un_set() -> None:
    elements = {
        Dimension("secteur", "Secteur"),
        ValeurDimension("secteur", "alpha", "Alpha"),
        Coordonnee("secteur", "alpha"),
        EspaceDimensionnel(frozenset({Coordonnee("secteur", "alpha")})),
    }
    assert len(elements) == 4


def test_coordonnees_espace_non_mutables() -> None:
    e = EspaceDimensionnel(frozenset({Coordonnee("secteur", "alpha")}))
    assert isinstance(e.coordonnees, frozenset)
    with pytest.raises(AttributeError):
        e.coordonnees.add(Coordonnee("societe", "x"))  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Cas limites : 1 / 2 / 3 dimensions
# ---------------------------------------------------------------------------


def test_espace_une_dimension_commune() -> None:
    e = EspaceDimensionnel(frozenset({Coordonnee("secteur", "alpha")}))
    assert e.signature == "secteur=alpha"
    assert len(e.coordonnees) == 1


def test_espace_deux_dimensions_pme() -> None:
    e = EspaceDimensionnel(
        frozenset({Coordonnee("secteur", "alpha"), Coordonnee("societe", "finance")})
    )
    assert e.signature == "secteur=alpha;societe=finance"


def test_espace_trois_dimensions_holding() -> None:
    e = EspaceDimensionnel(
        frozenset(
            {
                Coordonnee("secteur", "alpha"),
                Coordonnee("societe", "finance"),
                Coordonnee("departement", "compta"),
            }
        )
    )
    assert e.signature == "departement=compta;secteur=alpha;societe=finance"
    assert e.valeur_sur("departement") == "compta"
