"""Value object Modèle — gabarit d'un espace transverse (spec §5.2/§5.3).

100% stdlib, frozen dataclasses, immuables. Aucun import externe (garde-fou de
pureté : swisspipe/tests/test_core_purity.py, CLAUDE.md §1/§5).

Le Modèle est le GABARIT : il décide la STRUCTURE (arborescence imposée) et le cadre
métier (schéma de métadonnées, rôles). Il ne nomme JAMAIS une personne (INV-1) — il
plafonne OÙ et QUOI, jamais QUI. L'Instance (instance.py) matérialise un modèle en un
projet réel.

Sérialisation jsonb round-trippable (moule Matrice/Octroi) :
    {"id": "immobilier", "nom": "Projet immobilier",
     "arborescence_imposee": {"dossiers": [{"cle": "plans", "libelle": "Plans"}],
                              "dossiers_libres_autorises": true},
     "schema_metadonnees": [{"cle": "adresse", "libelle": "Adresse", "type": "texte",
                             "systeme_reference": "humain"}],
     "roles": ["chef_de_projet"]}
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from swisspipe.core.domain.matrice import Matrice


class SystemeReference(Enum):
    """Provenance d'un champ de métadonnée (spec §5.2) : saisi humain, poussé par une
    API (ex. ERP), ou les deux. La valeur de chaque membre est le token jsonb."""

    HUMAIN = "humain"
    API = "api"
    MIXTE = "mixte"


class PolitiqueDroits(Enum):
    """Curseur de gouvernance des droits d'un modèle (spec §5.4).

    - IMPOSEE : les droits viennent des rôles (matrice par rôle) — étape 3.
    - DELEGUEE : un admin attribue lui-même des droits, borné par un plafond — étape 5.
    - LIBRE : cas dégénéré, sans logique dédiée (traité comme DELEGUEE).
    """

    IMPOSEE = "imposee"
    DELEGUEE = "deleguee"
    LIBRE = "libre"


@dataclass(frozen=True)
class DossierImpose:
    """Un dossier du squelette imposé. Identité = `cle` ; `libelle` = nom affiché."""

    cle: str
    libelle: str

    def __post_init__(self) -> None:
        if not self.cle:
            raise ValueError("un dossier imposé exige une clé non vide")


@dataclass(frozen=True)
class ChampMeta:
    """Un champ du schéma de métadonnées. `systeme_reference` OBLIGATOIRE et typé."""

    cle: str
    libelle: str
    type: str
    systeme_reference: SystemeReference

    def __post_init__(self) -> None:
        if not self.cle:
            raise ValueError("un champ de métadonnée exige une clé non vide")
        if not isinstance(self.systeme_reference, SystemeReference):
            raise TypeError(
                "systeme_reference doit être un SystemeReference, "
                f"reçu {type(self.systeme_reference)!r}"
            )

    def vers_jsonb(self) -> dict[str, Any]:
        return {
            "cle": self.cle,
            "libelle": self.libelle,
            "type": self.type,
            "systeme_reference": self.systeme_reference.value,
        }

    @classmethod
    def depuis_jsonb(cls, data: Mapping[str, Any]) -> ChampMeta:
        return cls(
            cle=data["cle"],
            libelle=data["libelle"],
            type=data["type"],
            systeme_reference=SystemeReference(data["systeme_reference"]),
        )


@dataclass(frozen=True)
class ArborescenceImposee:
    """Squelette imposé d'un modèle : dossiers gelés + tolérance aux dossiers libres.

    Règles : au moins un dossier, clés uniques (spec §5.3, le squelette est structurel).
    """

    dossiers: tuple[DossierImpose, ...]
    dossiers_libres_autorises: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "dossiers", tuple(self.dossiers))
        if not self.dossiers:
            raise ValueError("une arborescence imposée ne peut pas être vide")
        for d in self.dossiers:
            if not isinstance(d, DossierImpose):
                raise TypeError(f"dossier invalide : {type(d)!r}")
        cles = [d.cle for d in self.dossiers]
        doublons = sorted({c for c in cles if cles.count(c) > 1})
        if doublons:
            raise ValueError(f"clés de dossier en double : {', '.join(doublons)}")

    @property
    def cles(self) -> frozenset[str]:
        return frozenset(d.cle for d in self.dossiers)

    def vers_jsonb(self) -> dict[str, Any]:
        return {
            "dossiers": [{"cle": d.cle, "libelle": d.libelle} for d in self.dossiers],
            "dossiers_libres_autorises": self.dossiers_libres_autorises,
        }

    @classmethod
    def depuis_jsonb(cls, data: Mapping[str, Any]) -> ArborescenceImposee:
        return cls(
            dossiers=tuple(
                DossierImpose(cle=d["cle"], libelle=d["libelle"]) for d in data["dossiers"]
            ),
            dossiers_libres_autorises=bool(data["dossiers_libres_autorises"]),
        )


@dataclass(frozen=True)
class Modele:
    """Gabarit d'espace transverse : structure imposée + schéma métier + rôles.

    Immuable. Décide OÙ (arborescence) et le cadre métier (schéma/rôles), jamais QUI
    (INV-1). `id` opaque, propriété de SwissPipe.
    """

    id: str
    nom: str
    arborescence_imposee: ArborescenceImposee
    schema_metadonnees: tuple[ChampMeta, ...] = field(default_factory=tuple)
    roles: tuple[str, ...] = field(default_factory=tuple)
    # Matrice IMPOSÉE par rôle (spec §5.4) : {rôle → {dossier → Matrice L1}}. Additif
    # (défaut vide -> rétrocompatible). Ne cite que des rôles/dossiers du modèle.
    matrice_par_role: Mapping[str, Mapping[str, Matrice]] = field(default_factory=dict)
    # Curseur de gouvernance (spec §5.4). Additif, défaut IMPOSEE (rétrocompat étapes 1-4).
    politique_droits: PolitiqueDroits = PolitiqueDroits.IMPOSEE

    def __post_init__(self) -> None:
        object.__setattr__(self, "schema_metadonnees", tuple(self.schema_metadonnees))
        object.__setattr__(self, "roles", tuple(self.roles))
        if not isinstance(self.politique_droits, PolitiqueDroits):
            recu = type(self.politique_droits)
            raise TypeError(f"politique_droits doit être un PolitiqueDroits, reçu {recu!r}")
        object.__setattr__(
            self,
            "matrice_par_role",
            {role: dict(par_dossier) for role, par_dossier in self.matrice_par_role.items()},
        )
        if not isinstance(self.arborescence_imposee, ArborescenceImposee):
            raise TypeError(
                "arborescence_imposee doit être une ArborescenceImposee, "
                f"reçu {type(self.arborescence_imposee)!r}"
            )
        for c in self.schema_metadonnees:
            if not isinstance(c, ChampMeta):
                raise TypeError(f"champ de schéma invalide : {type(c)!r}")
        self._valider_matrice_par_role()

    def _valider_matrice_par_role(self) -> None:
        """La matrice par rôle ne cite que des rôles ET des dossiers du modèle."""
        roles = set(self.roles)
        dossiers = set(self.arborescence_imposee.cles)
        for role_cle, par_dossier in self.matrice_par_role.items():
            if role_cle not in roles:
                raise ValueError(f"matrice par rôle : rôle inconnu du modèle : {role_cle!r}")
            for dossier_cle, matrice in par_dossier.items():
                if dossier_cle not in dossiers:
                    raise ValueError(
                        f"matrice par rôle : ressource inconnue du modèle : {dossier_cle!r}"
                    )
                if not isinstance(matrice, Matrice):
                    raise TypeError(
                        f"plafond de rôle doit être une Matrice, reçu {type(matrice)!r}"
                    )

    @property
    def cles_schema(self) -> frozenset[str]:
        return frozenset(c.cle for c in self.schema_metadonnees)

    def valider_metadonnees(self, metadonnees: Mapping[str, Any]) -> None:
        """Rejette (ValueError) des métadonnées non conformes au schéma.

        Conformité = clés fournies EXACTEMENT égales aux clés du schéma : ni clé
        manquante (le gabarit exige tous ses champs), ni clé inconnue (pas de champ
        hors schéma). Ne valide pas encore le type/provenance de chaque valeur (L2+).
        """
        fournies = set(metadonnees)
        attendues = set(self.cles_schema)
        manquantes = sorted(attendues - fournies)
        if manquantes:
            raise ValueError(
                f"métadonnées non conformes — clés manquantes : {', '.join(manquantes)}"
            )
        inconnues = sorted(fournies - attendues)
        if inconnues:
            raise ValueError(f"métadonnées non conformes — clés inconnues : {', '.join(inconnues)}")

    def vers_jsonb(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "nom": self.nom,
            "arborescence_imposee": self.arborescence_imposee.vers_jsonb(),
            "schema_metadonnees": [c.vers_jsonb() for c in self.schema_metadonnees],
            "roles": list(self.roles),
            "matrice_par_role": {
                role: {dossier: m.vers_jsonb() for dossier, m in par_dossier.items()}
                for role, par_dossier in self.matrice_par_role.items()
            },
            "politique_droits": self.politique_droits.value,
        }

    @classmethod
    def depuis_jsonb(cls, data: Mapping[str, Any]) -> Modele:
        schema: Iterable[Mapping[str, Any]] = data.get("schema_metadonnees", ())
        matrice_par_role = {
            role: {dossier: Matrice.depuis_jsonb(m) for dossier, m in par_dossier.items()}
            for role, par_dossier in data.get("matrice_par_role", {}).items()
        }
        return cls(
            id=data["id"],
            nom=data["nom"],
            arborescence_imposee=ArborescenceImposee.depuis_jsonb(data["arborescence_imposee"]),
            schema_metadonnees=tuple(ChampMeta.depuis_jsonb(c) for c in schema),
            roles=tuple(data.get("roles", ())),
            matrice_par_role=matrice_par_role,
            politique_droits=PolitiqueDroits(data.get("politique_droits", "imposee")),
        )
