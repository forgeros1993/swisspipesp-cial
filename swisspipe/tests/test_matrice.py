"""Tests des value objects de la matrice de droits (core/domain/matrice.py)."""

from __future__ import annotations

import dataclasses

import pytest

from swisspipe.core.domain.matrice import (
    DroitAdditionnel,
    Matrice,
    Mode,
    NiveauPrincipal,
)

# ---------------------------------------------------------------------------
# Relation d'ordre INCLUSIVE des niveaux : Lecture ⊂ Écriture ⊂ Suppression
# ---------------------------------------------------------------------------


def test_suppression_inclut_ecriture_et_lecture() -> None:
    assert NiveauPrincipal.SUPPRESSION.inclut(NiveauPrincipal.ECRITURE)
    assert NiveauPrincipal.SUPPRESSION.inclut(NiveauPrincipal.LECTURE)


def test_ecriture_inclut_lecture_mais_pas_suppression() -> None:
    assert NiveauPrincipal.ECRITURE.inclut(NiveauPrincipal.LECTURE)
    assert not NiveauPrincipal.ECRITURE.inclut(NiveauPrincipal.SUPPRESSION)


def test_lecture_n_inclut_que_lecture() -> None:
    assert NiveauPrincipal.LECTURE.inclut(NiveauPrincipal.LECTURE)
    assert not NiveauPrincipal.LECTURE.inclut(NiveauPrincipal.ECRITURE)
    assert not NiveauPrincipal.LECTURE.inclut(NiveauPrincipal.SUPPRESSION)


def test_inclusion_reflexive() -> None:
    for niveau in NiveauPrincipal:
        assert niveau.inclut(niveau)


def test_rang_strictement_croissant() -> None:
    assert (
        NiveauPrincipal.LECTURE.rang
        < NiveauPrincipal.ECRITURE.rang
        < NiveauPrincipal.SUPPRESSION.rang
    )


# ---------------------------------------------------------------------------
# Additionnels INDÉPENDANTS
# ---------------------------------------------------------------------------


def test_additionnels_sont_independants() -> None:
    # Avoir CLASSEMENT n'implique pas CREATION ni TELECHARGEMENT.
    m = Matrice(NiveauPrincipal.LECTURE, frozenset({DroitAdditionnel.CLASSEMENT}))
    assert DroitAdditionnel.CLASSEMENT in m.additionnels
    assert DroitAdditionnel.CREATION not in m.additionnels
    assert DroitAdditionnel.TELECHARGEMENT not in m.additionnels


def test_trois_additionnels_distincts() -> None:
    assert len(set(DroitAdditionnel)) == 3


# ---------------------------------------------------------------------------
# Immutabilité
# ---------------------------------------------------------------------------


def test_matrice_est_frozen() -> None:
    m = Matrice(NiveauPrincipal.ECRITURE)
    with pytest.raises(dataclasses.FrozenInstanceError):
        m.niveau = NiveauPrincipal.SUPPRESSION  # type: ignore[misc]


def test_additionnels_non_mutables() -> None:
    m = Matrice(NiveauPrincipal.LECTURE, {DroitAdditionnel.CREATION})
    assert isinstance(m.additionnels, frozenset)
    with pytest.raises(AttributeError):
        m.additionnels.add(DroitAdditionnel.CLASSEMENT)  # type: ignore[attr-defined]


def test_matrice_hachable() -> None:
    m1 = Matrice(NiveauPrincipal.ECRITURE, {DroitAdditionnel.CLASSEMENT})
    m2 = Matrice(NiveauPrincipal.ECRITURE, {DroitAdditionnel.CLASSEMENT})
    assert m1 == m2
    assert hash(m1) == hash(m2)
    assert len({m1, m2}) == 1


def test_coercition_set_vers_frozenset() -> None:
    # Construire avec un set ordinaire ne casse ni l'immutabilité ni l'égalité.
    m = Matrice(NiveauPrincipal.LECTURE, {DroitAdditionnel.CREATION})
    assert m.additionnels == frozenset({DroitAdditionnel.CREATION})


# ---------------------------------------------------------------------------
# couvre() — base du calcul de plafond
# ---------------------------------------------------------------------------


def test_couvre_niveau_superieur_et_additionnels_inclus() -> None:
    plafond = Matrice(
        NiveauPrincipal.SUPPRESSION,
        {DroitAdditionnel.CREATION, DroitAdditionnel.CLASSEMENT},
    )
    demande = Matrice(NiveauPrincipal.ECRITURE, {DroitAdditionnel.CLASSEMENT})
    assert plafond.couvre(demande)


def test_ne_couvre_pas_si_additionnel_manquant() -> None:
    plafond = Matrice(NiveauPrincipal.SUPPRESSION, {DroitAdditionnel.CREATION})
    demande = Matrice(NiveauPrincipal.LECTURE, {DroitAdditionnel.TELECHARGEMENT})
    assert not plafond.couvre(demande)


def test_ne_couvre_pas_si_niveau_inferieur() -> None:
    plafond = Matrice(NiveauPrincipal.LECTURE)
    demande = Matrice(NiveauPrincipal.ECRITURE)
    assert not plafond.couvre(demande)


def test_couvre_reflexif() -> None:
    m = Matrice(NiveauPrincipal.ECRITURE, {DroitAdditionnel.CLASSEMENT})
    assert m.couvre(m)


# ---------------------------------------------------------------------------
# fusionner() — union des droits (combinaison multi-groupes additive)
# ---------------------------------------------------------------------------


def test_fusionner_prend_niveau_max_et_additionnels_unis() -> None:
    a = Matrice(NiveauPrincipal.LECTURE, {DroitAdditionnel.CREATION})
    b = Matrice(NiveauPrincipal.ECRITURE, {DroitAdditionnel.CLASSEMENT})
    attendu = Matrice(
        NiveauPrincipal.ECRITURE, {DroitAdditionnel.CREATION, DroitAdditionnel.CLASSEMENT}
    )
    assert a.fusionner(b) == attendu


def test_fusionner_commutatif() -> None:
    a = Matrice(NiveauPrincipal.LECTURE, {DroitAdditionnel.CREATION})
    b = Matrice(NiveauPrincipal.SUPPRESSION)
    assert a.fusionner(b) == b.fusionner(a)


def test_fusionner_idempotent() -> None:
    m = Matrice(NiveauPrincipal.ECRITURE, {DroitAdditionnel.TELECHARGEMENT})
    assert m.fusionner(m) == m


def test_fusionner_ne_retire_aucun_droit() -> None:
    a = Matrice(NiveauPrincipal.SUPPRESSION, {DroitAdditionnel.CREATION})
    b = Matrice(NiveauPrincipal.LECTURE)
    fusion = a.fusionner(b)
    assert fusion.couvre(a) and fusion.couvre(b)


# ---------------------------------------------------------------------------
# Sérialisation jsonb (round-trip)
# ---------------------------------------------------------------------------


def test_vers_jsonb_format_spec() -> None:
    m = Matrice(NiveauPrincipal.ECRITURE, {DroitAdditionnel.CLASSEMENT})
    assert m.vers_jsonb() == {"niveau": "ecriture", "additionnels": ["classement"]}


def test_vers_jsonb_additionnels_tries() -> None:
    m = Matrice(
        NiveauPrincipal.SUPPRESSION,
        {DroitAdditionnel.TELECHARGEMENT, DroitAdditionnel.CLASSEMENT, DroitAdditionnel.CREATION},
    )
    # Sortie déterministe (triée alphabétiquement par token).
    assert m.vers_jsonb()["additionnels"] == ["classement", "creation", "telechargement"]


@pytest.mark.parametrize(
    "matrice",
    [
        Matrice(NiveauPrincipal.LECTURE),
        Matrice(NiveauPrincipal.ECRITURE, {DroitAdditionnel.CLASSEMENT}),
        Matrice(
            NiveauPrincipal.SUPPRESSION,
            {
                DroitAdditionnel.CREATION,
                DroitAdditionnel.CLASSEMENT,
                DroitAdditionnel.TELECHARGEMENT,
            },
        ),
    ],
)
def test_round_trip_jsonb(matrice: Matrice) -> None:
    assert Matrice.depuis_jsonb(matrice.vers_jsonb()) == matrice


def test_depuis_jsonb_sans_cle_additionnels() -> None:
    # Clé absente -> ensemble vide (robustesse de lecture).
    m = Matrice.depuis_jsonb({"niveau": "lecture"})
    assert m == Matrice(NiveauPrincipal.LECTURE)


# ---------------------------------------------------------------------------
# Cas limites & validation
# ---------------------------------------------------------------------------


def test_matrice_vide_d_additionnels() -> None:
    m = Matrice(NiveauPrincipal.LECTURE)
    assert m.additionnels == frozenset()
    assert m.vers_jsonb() == {"niveau": "lecture", "additionnels": []}


def test_niveau_minimal_est_lecture() -> None:
    assert min(NiveauPrincipal, key=lambda n: n.rang) is NiveauPrincipal.LECTURE


def test_niveau_invalide_leve_typeerror() -> None:
    with pytest.raises(TypeError):
        Matrice("ecriture")  # type: ignore[arg-type]


def test_additionnel_invalide_leve_typeerror() -> None:
    with pytest.raises(TypeError):
        Matrice(NiveauPrincipal.LECTURE, {"classement"})  # type: ignore[arg-type]


def test_tokens_enums() -> None:
    assert NiveauPrincipal.ECRITURE.value == "ecriture"
    assert DroitAdditionnel.TELECHARGEMENT.value == "telechargement"
    assert {m.value for m in Mode} == {"heriter", "modifier", "refuser"}
