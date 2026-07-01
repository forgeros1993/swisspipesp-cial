"""Tests du value object Modèle (core/domain/modele.py) — gabarit des espaces transverses.

Le Modèle DÉCIDE la structure (arborescence imposée) + le plafond métier (schéma de
métadonnées, rôles) — JAMAIS QUI (INV-1). 100% domaine pur (frozen dataclasses stdlib).
"""

from __future__ import annotations

import dataclasses

import pytest

from swisspipe.core.domain.modele import (
    ArborescenceImposee,
    ChampMeta,
    DossierImpose,
    Modele,
    SystemeReference,
)


def _modele_immobilier() -> Modele:
    """« Projet immobilier » : 3 dossiers imposés + schéma minimal."""
    return Modele(
        id="immobilier",
        nom="Projet immobilier",
        arborescence_imposee=ArborescenceImposee(
            dossiers=[
                DossierImpose(cle="plans", libelle="Plans"),
                DossierImpose(cle="correspondance", libelle="Correspondance"),
                DossierImpose(cle="divers", libelle="Divers"),
            ],
            dossiers_libres_autorises=True,
        ),
        schema_metadonnees=[
            ChampMeta(
                cle="adresse",
                libelle="Adresse",
                type="texte",
                systeme_reference=SystemeReference.HUMAIN,
            ),
            ChampMeta(
                cle="ref_odoo",
                libelle="Réf Odoo",
                type="texte",
                systeme_reference=SystemeReference.API,
            ),
        ],
        roles=["chef_de_projet", "collaborateur"],
    )


# ---------------------------------------------------------------------------
# Construction valide
# ---------------------------------------------------------------------------


def test_modele_valide_construit() -> None:
    m = _modele_immobilier()
    assert m.id == "immobilier"
    assert m.nom == "Projet immobilier"
    assert len(m.arborescence_imposee.dossiers) == 3
    assert m.arborescence_imposee.dossiers_libres_autorises is True
    assert [c.cle for c in m.schema_metadonnees] == ["adresse", "ref_odoo"]
    assert m.roles == ("chef_de_projet", "collaborateur")


# ---------------------------------------------------------------------------
# Arborescence : vide et clés dupliquées rejetées
# ---------------------------------------------------------------------------


def test_arborescence_vide_rejetee() -> None:
    with pytest.raises(ValueError, match="vide"):
        ArborescenceImposee(dossiers=[], dossiers_libres_autorises=False)


def test_dossiers_cles_dupliquees_rejetees() -> None:
    with pytest.raises(ValueError, match="double"):
        ArborescenceImposee(
            dossiers=[
                DossierImpose(cle="plans", libelle="Plans"),
                DossierImpose(cle="plans", libelle="Plans bis"),
            ],
            dossiers_libres_autorises=False,
        )


# ---------------------------------------------------------------------------
# systeme_reference : obligatoire + dans l'enum
# ---------------------------------------------------------------------------


def test_systeme_reference_valeurs() -> None:
    assert {s.value for s in SystemeReference} == {"humain", "api", "mixte"}


def test_champ_meta_sans_systeme_reference_rejete() -> None:
    with pytest.raises(TypeError):
        ChampMeta(cle="x", libelle="X", type="texte")  # type: ignore[call-arg]


def test_champ_meta_systeme_reference_hors_enum_rejete() -> None:
    with pytest.raises(TypeError, match="SystemeReference"):
        ChampMeta(cle="x", libelle="X", type="texte", systeme_reference="humain")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Conformité des métadonnées au schéma
# ---------------------------------------------------------------------------


def test_metadonnees_conformes_acceptees() -> None:
    m = _modele_immobilier()
    m.valider_metadonnees({"adresse": "Chemin des Roses 12", "ref_odoo": "SO-42"})  # ne lève pas


def test_metadonnees_cle_manquante_rejetee() -> None:
    m = _modele_immobilier()
    with pytest.raises(ValueError, match="ref_odoo"):
        m.valider_metadonnees({"adresse": "Chemin des Roses 12"})


def test_metadonnees_cle_inconnue_rejetee() -> None:
    m = _modele_immobilier()
    with pytest.raises(ValueError, match="inconnu"):
        m.valider_metadonnees({"adresse": "X", "ref_odoo": "Y", "pirate": "Z"})


# ---------------------------------------------------------------------------
# Round-trip jsonb (moule Matrice/Octroi)
# ---------------------------------------------------------------------------


def test_round_trip_jsonb() -> None:
    m = _modele_immobilier()
    assert Modele.depuis_jsonb(m.vers_jsonb()) == m


# ---------------------------------------------------------------------------
# Immuabilité
# ---------------------------------------------------------------------------


def test_modele_est_frozen() -> None:
    m = _modele_immobilier()
    with pytest.raises(dataclasses.FrozenInstanceError):
        m.nom = "autre"  # type: ignore[misc]
