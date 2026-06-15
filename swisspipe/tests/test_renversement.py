"""Tests du service de renversement (core/services/renversement.py).

Reprend l'exemple du document : espaces A/B/C sur Secteur/Société/Département.
Le Secteur est une dimension HORS-ORDRE dans les exemples (présente sur les espaces,
absente des ordres testés) — elle prouve que des dimensions non pivotées n'affectent
pas le regroupement.
"""

from __future__ import annotations

from swisspipe.core.domain.topologie import Coordonnee, EspaceDimensionnel
from swisspipe.core.services.renversement import (
    NON_DEFINI,
    ArbreNavigation,
    NoeudNavigation,
    renverser,
)

# --- Espaces de l'exemple --------------------------------------------------
A = EspaceDimensionnel(
    frozenset(
        {
            Coordonnee("secteur", "sec1"),
            Coordonnee("societe", "alpha"),
            Coordonnee("departement", "technique"),
        }
    )
)
B = EspaceDimensionnel(
    frozenset(
        {
            Coordonnee("secteur", "sec1"),
            Coordonnee("societe", "alpha"),
            Coordonnee("departement", "finance"),
        }
    )
)
C = EspaceDimensionnel(
    frozenset(
        {
            Coordonnee("secteur", "sec1"),
            Coordonnee("societe", "gamma"),
            Coordonnee("departement", "finance"),
        }
    )
)

LIBELLES = {
    ("societe", "alpha"): "Alpha",
    ("societe", "gamma"): "Gamma",
    ("departement", "technique"): "Technique",
    ("departement", "finance"): "Finance",
}


def _par_libelle(noeuds: tuple[NoeudNavigation, ...]) -> dict[str, NoeudNavigation]:
    return {n.libelle: n for n in noeuds}


# ---------------------------------------------------------------------------
# Voir par Société → Département
# ---------------------------------------------------------------------------


def test_societe_puis_departement() -> None:
    arbre = renverser([A, B, C], ["societe", "departement"], LIBELLES)
    assert arbre.ordre == ("societe", "departement")

    racine = _par_libelle(arbre.noeuds)
    assert set(racine) == {"Alpha", "Gamma"}
    # Nœuds intermédiaires : pas d'espace_id.
    assert racine["Alpha"].espace_id is None
    assert racine["Alpha"].dimension_cle == "societe"

    alpha = _par_libelle(racine["Alpha"].enfants)
    assert set(alpha) == {"Technique", "Finance"}
    assert alpha["Technique"].espace_id == A.signature
    assert alpha["Finance"].espace_id == B.signature
    assert alpha["Technique"].dimension_cle == "departement"
    assert alpha["Technique"].enfants == ()  # feuille

    gamma = _par_libelle(racine["Gamma"].enfants)
    assert set(gamma) == {"Finance"}
    assert gamma["Finance"].espace_id == C.signature


# ---------------------------------------------------------------------------
# Voir par Département → Société (arbre inversé)
# ---------------------------------------------------------------------------


def test_departement_puis_societe() -> None:
    arbre = renverser([A, B, C], ["departement", "societe"], LIBELLES)

    racine = _par_libelle(arbre.noeuds)
    assert set(racine) == {"Technique", "Finance"}

    technique = _par_libelle(racine["Technique"].enfants)
    assert set(technique) == {"Alpha"}
    assert technique["Alpha"].espace_id == A.signature

    finance = _par_libelle(racine["Finance"].enfants)
    assert set(finance) == {"Alpha", "Gamma"}
    assert finance["Alpha"].espace_id == B.signature
    assert finance["Gamma"].espace_id == C.signature


# ---------------------------------------------------------------------------
# Projection : mêmes entrées, deux arbres différents, rien ne bouge
# ---------------------------------------------------------------------------


def test_meme_entree_deux_projections_differentes() -> None:
    espaces = [A, B, C]
    avant = set(espaces)
    arbre1 = renverser(espaces, ["societe", "departement"], LIBELLES)
    arbre2 = renverser(espaces, ["departement", "societe"], LIBELLES)
    assert arbre1 != arbre2
    # Les espaces d'entrée ne sont pas modifiés (projection pure).
    assert set(espaces) == avant
    assert espaces == [A, B, C]


# ---------------------------------------------------------------------------
# Déterminisme
# ---------------------------------------------------------------------------


def test_determinisme() -> None:
    a1 = renverser([A, B, C], ["societe", "departement"], LIBELLES)
    a2 = renverser([C, B, A], ["societe", "departement"], LIBELLES)  # ordre d'entrée différent
    assert a1 == a2


def test_enfants_tries_par_libelle() -> None:
    arbre = renverser([A, B, C], ["societe", "departement"], LIBELLES)
    alpha = _par_libelle(arbre.noeuds)["Alpha"]
    libelles_enfants = [n.libelle for n in alpha.enfants]
    assert libelles_enfants == sorted(libelles_enfants)
    # Finance avant Technique.
    assert libelles_enfants == ["Finance", "Technique"]


# ---------------------------------------------------------------------------
# Libellés : utilisés si fournis, fallback sur la clé sinon
# ---------------------------------------------------------------------------


def test_fallback_sur_cle_sans_libelle() -> None:
    arbre = renverser([A, B, C], ["societe"], libelles=None)
    libelles = {n.libelle for n in arbre.noeuds}
    assert libelles == {"alpha", "gamma"}  # clés brutes, pas "Alpha"/"Gamma"


def test_libelle_partiel() -> None:
    # Libellé fourni pour alpha seulement -> gamma retombe sur sa clé.
    arbre = renverser([A, B, C], ["societe"], {("societe", "alpha"): "Alpha"})
    assert {n.libelle for n in arbre.noeuds} == {"Alpha", "gamma"}


# ---------------------------------------------------------------------------
# Pivot partiel (ordre à 1 dimension)
# ---------------------------------------------------------------------------


def test_pivot_partiel_une_dimension() -> None:
    arbre = renverser([A, B, C], ["societe"], LIBELLES)
    racine = _par_libelle(arbre.noeuds)
    assert set(racine) == {"Alpha", "Gamma"}

    # alpha contient 2 espaces (A,B) -> nœud intermédiaire + 2 feuilles distinctes.
    alpha = racine["Alpha"]
    assert alpha.espace_id is None
    assert len(alpha.enfants) == 2
    assert {f.espace_id for f in alpha.enfants} == {A.signature, B.signature}

    # gamma contient 1 espace (C) -> feuille directe.
    gamma = racine["Gamma"]
    assert gamma.espace_id == C.signature
    assert gamma.enfants == ()


# ---------------------------------------------------------------------------
# Dimension absente -> nœud "(non défini)"
# ---------------------------------------------------------------------------


def test_dimension_absente_groupe_sous_non_defini() -> None:
    d = EspaceDimensionnel(frozenset({Coordonnee("departement", "finance")}))  # pas de societe
    arbre = renverser([A, d], ["societe"], LIBELLES)
    racine = _par_libelle(arbre.noeuds)
    assert NON_DEFINI in racine
    noeud_nd = racine[NON_DEFINI]
    assert noeud_nd.valeur_cle == NON_DEFINI
    assert noeud_nd.espace_id == d.signature  # d seul sous (non défini) -> feuille
    # A reste correctement sous Alpha.
    assert racine["Alpha"].espace_id == A.signature


# ---------------------------------------------------------------------------
# Feuilles vs nœuds intermédiaires : espace_id
# ---------------------------------------------------------------------------


def test_intermediaires_sans_espace_id_feuilles_avec() -> None:
    arbre = renverser([A, B, C], ["societe", "departement"], LIBELLES)
    for noeud_inter in arbre.noeuds:
        assert noeud_inter.espace_id is None
        assert noeud_inter.enfants != ()
        for feuille in noeud_inter.enfants:
            assert feuille.espace_id is not None
            assert feuille.enfants == ()


# ---------------------------------------------------------------------------
# Ordre vide -> arbre vide
# ---------------------------------------------------------------------------


def test_ordre_vide_arbre_vide() -> None:
    arbre = renverser([A, B, C], [])
    assert arbre == ArbreNavigation(noeuds=(), ordre=())
    assert arbre.noeuds == ()
