"""Tests de l'Instance (core/domain/instance.py) — un projet réel = espace transverse.

Instancier un Modèle matérialise son squelette imposé en ressources ABSTRAITES
rattachées à l'instance (pas de vrais dossiers Nextcloud — on reste dans le cœur).
Le squelette est GELÉ (spec §5.3) : ni renommé ni supprimé, même par un droit de
Suppression. 100% domaine pur.
"""

from __future__ import annotations

import pytest

from swisspipe.core.domain.instance import (
    NATURE_TRANSVERSE,
    Instance,
    SqueletteGeleError,
    instancier,
)
from swisspipe.core.domain.matrice import Matrice, NiveauPrincipal
from swisspipe.core.domain.modele import (
    ArborescenceImposee,
    ChampMeta,
    DossierImpose,
    Modele,
    SystemeReference,
)
from swisspipe.core.domain.ressource import Ressource

SUPPRESSION = Matrice(NiveauPrincipal.SUPPRESSION)


def _modele(*, libres: bool = True) -> Modele:
    return Modele(
        id="immobilier",
        nom="Projet immobilier",
        arborescence_imposee=ArborescenceImposee(
            dossiers=[
                DossierImpose(cle="plans", libelle="Plans"),
                DossierImpose(cle="correspondance", libelle="Correspondance"),
                DossierImpose(cle="divers", libelle="Divers"),
            ],
            dossiers_libres_autorises=libres,
        ),
        schema_metadonnees=[
            ChampMeta(
                cle="adresse",
                libelle="Adresse",
                type="texte",
                systeme_reference=SystemeReference.HUMAIN,
            ),
        ],
        roles=["chef_de_projet"],
    )


def _instance(*, libres: bool = True) -> Instance:
    return instancier(
        _modele(libres=libres),
        nom="Chemin des Roses 12",
        metadonnees={"adresse": "Chemin des Roses 12"},
        instance_id="inst-1",
    )


# ---------------------------------------------------------------------------
# §2 — Instanciation : le squelette imposé devient des ressources abstraites
# ---------------------------------------------------------------------------


def test_instancier_cree_les_ressources_du_squelette() -> None:
    inst = _instance()
    assert {r.chemin for r in inst.ressources_squelette} == {
        "/Plans",
        "/Correspondance",
        "/Divers",
    }
    # ressources abstraites rattachées à l'instance (espace_id = id de l'instance).
    assert all(r.espace_id == "inst-1" for r in inst.ressources_squelette)
    assert all(r.type == "folder" for r in inst.ressources_squelette)


def test_instance_est_espace_transverse_avec_modele_id() -> None:
    inst = _instance()
    assert inst.nature == NATURE_TRANSVERSE == "transverse"
    assert inst.modele_id == "immobilier"


def test_metadonnees_non_conformes_rejetees() -> None:
    with pytest.raises(ValueError, match="non conformes"):
        instancier(
            _modele(),
            nom="X",
            metadonnees={},  # 'adresse' manquante
            instance_id="inst-2",
        )


def test_cle_reconciliation_reservee_sans_logique() -> None:
    # Champ réservé au futur ID Odoo : juste stocké, aucune logique ERP.
    assert _instance().cle_reconciliation is None
    inst = instancier(
        _modele(),
        nom="X",
        metadonnees={"adresse": "A"},
        instance_id="inst-3",
        cle_reconciliation="SO-42",
    )
    assert inst.cle_reconciliation == "SO-42"


# ---------------------------------------------------------------------------
# §3 — Squelette gelé : ni renommé ni supprimé, même par un droit de Suppression
# ---------------------------------------------------------------------------


def _id_squelette(inst: Instance, cle: str) -> str:
    return next(r.id for r in inst.ressources_squelette if r.chemin == cle)


def test_supprimer_dossier_squelette_refuse_meme_avec_suppression() -> None:
    inst = _instance()
    cible = _id_squelette(inst, "/Plans")
    with pytest.raises(SqueletteGeleError, match="gel"):
        # Un droit de Suppression NE PEUT PAS lever le gel (règle structurelle §5.3).
        inst.supprimer_ressource(cible, matrice_droit=SUPPRESSION)


def test_renommer_dossier_squelette_refuse() -> None:
    inst = _instance()
    cible = _id_squelette(inst, "/Plans")
    with pytest.raises(SqueletteGeleError, match="gel"):
        inst.renommer_ressource(cible, "/Plans renommé")
    # zéro écriture : l'instance d'origine est intacte.
    assert {r.chemin for r in inst.ressources_squelette} == {
        "/Plans",
        "/Correspondance",
        "/Divers",
    }


def test_dossier_libre_est_supprimable() -> None:
    inst = _instance(libres=True)
    libre = Ressource(id="inst-1:libre", type="folder", chemin="/Photos", espace_id="inst-1")
    inst2 = inst.ajouter_ressource_libre(libre)
    assert "/Photos" in {r.chemin for r in inst2.ressources_libres}

    inst3 = inst2.supprimer_ressource(libre.id)
    assert "/Photos" not in {r.chemin for r in inst3.ressources_libres}
    # Le squelette, lui, reste intact.
    assert len(inst3.ressources_squelette) == 3


def test_ajouter_dossier_libre_refuse_si_non_autorise() -> None:
    inst = _instance(libres=False)
    libre = Ressource(id="inst-1:libre", type="folder", chemin="/Photos", espace_id="inst-1")
    with pytest.raises(ValueError, match="libres"):
        inst.ajouter_ressource_libre(libre)
