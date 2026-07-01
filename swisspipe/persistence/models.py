"""Modèles SQLAlchemy 2.0 — persistance du périmètre L1 (spec §4, §10).

La persistance DÉPEND du domaine (elle mappe les value objects vers des lignes) et
peut importer SQLAlchemy. Le cœur (core/) n'importe JAMAIS la persistance — garde-fou
de pureté (test_core_purity.py, CLAUDE.md §1). C'est volontaire et à sens unique.

Périmètre L1 : dimensions, valeurs, espaces dimensionnels + coordonnées, groupes +
membres, ressources + mapping agnostique, octrois (état courant), journal (append-only).
Hors L1 : transverses / montages (L2).

État courant vs journal (§10) :
- `octroi` = ÉTAT COURANT, ne contient que le vivant (mutable : archivage/remplacement).
- `journal_acces` = JOURNAL append-only, immuable (trigger Postgres anti UPDATE/DELETE,
  cf. migration). Une correction se fait par une ligne compensatoire (INSERT), jamais
  par modification (§10.2, INV-6).
"""

from __future__ import annotations

import enum
import uuid
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from swisspipe.core.domain.acteurs import TypeGroupe
from swisspipe.core.domain.matrice import Mode
from swisspipe.core.domain.montage import EtatMontage
from swisspipe.core.domain.topologie import Coordonnee, EspaceDimensionnel

# Convention de nommage des contraintes -> migrations déterministes.
_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=_CONVENTION)


# --- Enums (tokens = valeurs du domaine, cf. matrice.py / acteurs.py) ----------


class NatureEspace(enum.Enum):
    DIMENSIONNEL = "dimensionnel"
    TRANSVERSE = "transverse"


class ActionJournal(enum.Enum):
    OCTROI = "octroi"
    REVOCATION = "revocation"
    GEL = "gel"
    DEGEL = "degel"
    MODIFICATION = "modification"


class TypeEvenement(enum.Enum):
    """Type d'événement de cycle de vie (journal_evenements). Enum EXTENSIBLE, étendu
    par migration ; le journal des DROITS (journal_acces) reste séparé et intouché."""

    INSTANCIATION = "instanciation"
    MONTAGE = "montage"
    DEMONTAGE = "demontage"


def _pg_enum(python_enum: type[enum.Enum], nom: str) -> Enum:
    """Enum Postgres natif stockant les .value (tokens), pas les .name."""
    return Enum(python_enum, name=nom, values_callable=lambda e: [m.value for m in e])


# --- Mixins -------------------------------------------------------------------


class _UUIDPk:
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )


class _Horodatage:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


# --- Topologie ----------------------------------------------------------------


class Dimension(Base, _UUIDPk, _Horodatage):
    __tablename__ = "dimension"

    cle: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    libelle: Mapped[str] = mapped_column(String, nullable=False)
    rang: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("dimension.id"), nullable=True
    )


class ValeurDimension(Base, _UUIDPk, _Horodatage):
    __tablename__ = "valeur_dimension"
    __table_args__ = (UniqueConstraint("dimension_id", "cle", name="uq_valeur_dimension_dim_cle"),)

    dimension_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("dimension.id"), nullable=False
    )
    cle: Mapped[str] = mapped_column(String, nullable=False)
    libelle: Mapped[str] = mapped_column(String, nullable=False)


class Espace(Base, _UUIDPk, _Horodatage):
    __tablename__ = "espace"

    nature: Mapped[NatureEspace] = mapped_column(
        _pg_enum(NatureEspace, "nature_espace"), nullable=False
    )
    archive: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    created_via: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Unicité de combinaison (§4.2) : signature dérivée, cohérente avec le domaine
    # (cf. signature_combinaison ci-dessous). Index unique = pas deux espaces au
    # même jeu de coordonnées. Pour un espace TRANSVERSE (instance) : signature
    # synthétique unique dérivée de l'id (pas de coordonnées) -> contrainte préservée.
    combinaison_signature: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    # --- Transverses (L2) : une instance est un espace nature='transverse' lié à son
    # modèle. Nullables -> les espaces dimensionnels L1 restent valides tels quels.
    modele_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("modele.id"), nullable=True
    )
    metadonnees: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    cle_reconciliation: Mapped[str | None] = mapped_column(Text, nullable=True)


class EspaceCoordonnee(Base):
    __tablename__ = "espace_coordonnee"

    # PK (espace_id, dimension_id) -> au plus une valeur par dimension dans un espace.
    espace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("espace.id"), primary_key=True
    )
    dimension_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("dimension.id"), primary_key=True
    )
    valeur_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("valeur_dimension.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# --- Acteurs ------------------------------------------------------------------


class Groupe(Base, _UUIDPk, _Horodatage):
    __tablename__ = "groupe"

    type: Mapped[TypeGroupe] = mapped_column(_pg_enum(TypeGroupe, "type_groupe"), nullable=False)
    cle: Mapped[str] = mapped_column(String, unique=True, nullable=False)


class GroupeMembre(Base):
    __tablename__ = "groupe_membre"

    groupe_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("groupe.id"), primary_key=True
    )
    compte_id: Mapped[str] = mapped_column(String, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# --- Ressources ---------------------------------------------------------------


class Ressource(Base, _UUIDPk, _Horodatage):
    __tablename__ = "ressource"

    # type = chaîne libre/extensible (folder/mailbox/door/…). AUCUN id externe ici
    # (agnosticité §3.3 : le mapping vit dans ressource_mapping).
    type: Mapped[str] = mapped_column(Text, nullable=False)
    espace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("espace.id"), nullable=False
    )
    chemin: Mapped[str] = mapped_column(Text, nullable=False)


class RessourceMapping(Base):
    __tablename__ = "ressource_mapping"

    # Indirection agnostique : interne (ressource_id) <-> externe (cle_externe),
    # par adaptateur. Géré par l'adaptateur, JAMAIS par la table ressource.
    ressource_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ressource.id"), primary_key=True
    )
    adaptateur: Mapped[str] = mapped_column(Text, primary_key=True)
    cle_externe: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


# --- Droits : état courant + journal -----------------------------------------


class Octroi(Base, _UUIDPk, _Horodatage):
    """ÉTAT COURANT. Un octroi vivant par (ressource, groupe)."""

    __tablename__ = "octroi"
    __table_args__ = (
        UniqueConstraint("ressource_id", "groupe_id", name="uq_octroi_ressource_groupe"),
    )

    ressource_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ressource.id"), nullable=False
    )
    groupe_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("groupe.id"), nullable=False
    )
    mode: Mapped[Mode] = mapped_column(_pg_enum(Mode, "mode_octroi"), nullable=False)
    # jsonb au format domaine : {"niveau": ..., "additionnels": [...]} ; null si
    # HERITER/REFUSER (cf. Octroi du domaine).
    matrice: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)


class JournalAcces(Base, _UUIDPk):
    """JOURNAL append-only (§10, INV-6).

    Pas de FK sur ressource/groupe : le journal est historique et doit survivre à
    l'archivage des entités. Immuable au niveau Postgres (trigger anti UPDATE/DELETE,
    cf. migration). Pas d'`updated_at` : une ligne ne change jamais.
    """

    __tablename__ = "journal_acces"

    ressource_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    groupe_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    action: Mapped[ActionJournal] = mapped_column(
        _pg_enum(ActionJournal, "action_journal"), nullable=False
    )
    matrice_avant: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    matrice_apres: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    cause: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    acteur: Mapped[str | None] = mapped_column(Text, nullable=True)
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# --- Transverses : gabarit (modèle) + journal des événements de cycle de vie -----


class Modele(Base, _UUIDPk, _Horodatage):
    """Gabarit d'un espace transverse (spec §5.2). Composants sérialisés en jsonb au
    format du domaine (core/domain/modele.py : Modele.vers_jsonb)."""

    __tablename__ = "modele"

    nom: Mapped[str] = mapped_column(Text, nullable=False)
    arborescence: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    schema_metadonnees: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    roles: Mapped[list[str]] = mapped_column(JSONB, nullable=False)


class JournalEvenement(Base, _UUIDPk):
    """JOURNAL append-only des ÉVÉNEMENTS de cycle de vie (instanciation, …).

    SÉPARÉ de journal_acces (journal des DROITS, INV-6) : un événement n'a ni groupe ni
    matrice. Pas de FK sur espace_id (historique, survit à l'archivage). Immuable au
    niveau Postgres (trigger anti UPDATE/DELETE, cf. migration 0003). Pas d'updated_at.
    """

    __tablename__ = "journal_evenements"

    espace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    type_evenement: Mapped[TypeEvenement] = mapped_column(
        _pg_enum(TypeEvenement, "type_evenement"), nullable=False
    )
    cause: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    acteur: Mapped[str | None] = mapped_column(Text, nullable=True)
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Montage(Base, _UUIDPk, _Horodatage):
    """Fenêtre d'une instance transverse sur un hôte (spec §4.4). Décide OÙ + PLAFOND,
    jamais QUI (INV-1) : aucune colonne de bénéficiaire.

    `portee` (§5.5) : jsonb {"chemins": [...]}. `matrice_plafond` : Matrice L1 sérialisée
    (pas de nouveau type). `consenti_par` : auteur du consentement de l'hôte (deux clés),
    PAS un bénéficiaire. `etat` : réutilise l'enum du domaine (montage.EtatMontage).
    """

    __tablename__ = "montage"

    espace_transverse_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("espace.id"), nullable=False
    )
    espace_hote_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("espace.id"), nullable=False
    )
    chemin_hote: Mapped[str] = mapped_column(Text, nullable=False)
    portee: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    matrice_plafond: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    consenti_par: Mapped[str] = mapped_column(Text, nullable=False)
    consenti_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    etat: Mapped[EtatMontage] = mapped_column(
        _pg_enum(EtatMontage, "etat_montage"), nullable=False, server_default=text("'actif'")
    )


class EtatSysteme(Base):
    """Marqueur clé/valeur MUTABLE (pas un journal) — ex. dernier état Nextcloud vu.

    Sert à la détection de changement (upgrade / réactivation de Group Folders) : on
    compare l'état courant au dernier état mémorisé sous une `cle` (ex. "nextcloud").
    """

    __tablename__ = "etat_systeme"

    cle: Mapped[str] = mapped_column(Text, primary_key=True)
    valeur: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


# --- Cohérence de signature domaine <-> persistance ---------------------------


def signature_combinaison(paires_dim_val_cles: Iterable[tuple[str, str]]) -> str:
    """Signature de combinaison d'un espace, alimentant `espace.combinaison_signature`.

    Réutilise DIRECTEMENT la logique du domaine (EspaceDimensionnel.signature) à
    partir des couples (dimension_cle, valeur_cle) — garantit que la signature
    persistée est IDENTIQUE à celle du domaine. Toute évolution du format se fait
    dans le domaine, la persistance suit automatiquement.
    """
    coords = frozenset(Coordonnee(dim, val) for dim, val in paires_dim_val_cles)
    return EspaceDimensionnel(coords).signature
