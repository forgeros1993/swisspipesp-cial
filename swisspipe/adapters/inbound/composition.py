"""Composition root de l'inbound — WIRING uniquement, zéro logique métier.

Assemble depuis la config (variables d'env) : une session SQLAlchemy (depuis DATABASE_URL)
+ un AdaptateurRessource (fake|nextcloud depuis SWISSPIPE_ADAPTER). Les commandes CLI/cron
récupèrent ces objets déjà câblés et appellent les services applicatifs existants.

Config (env, jamais codé en dur) :
- DATABASE_URL       : URL SQLAlchemy du cœur (jetable maintenant, dédié plus tard).
- SWISSPIPE_ADAPTER  : "fake" (mémoire, hermétique) | "nextcloud" (occ-over-SSH). Défaut "fake".
- (NC : la config SSH vit dans occ_runner — NEXTCLOUD_SSH_ALIAS / NEXTCLOUD_OCC_PATH.)
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from swisspipe.adapters.outbound.fake.adaptateur_memoire import AdaptateurMemoire
from swisspipe.adapters.outbound.fake.executeur_memoire import ExecuteurProjectionMemoire
from swisspipe.adapters.outbound.nextcloud.adaptateur_nextcloud import AdaptateurNextcloud
from swisspipe.adapters.outbound.nextcloud.executeur_projection import ExecuteurProjectionOcc
from swisspipe.adapters.outbound.nextcloud.occ_runner import executer_occ
from swisspipe.application.projection_service import ExecuteurProjection
from swisspipe.core.ports.adaptateur_ressource import AdaptateurRessource
from swisspipe.persistence.models import Montage


class ConfigurationError(RuntimeError):
    """Config d'inbound manquante ou invalide (pas une erreur métier)."""


def construire_adaptateur(nom: str | None = None) -> AdaptateurRessource:
    """Fabrique l'adaptateur depuis SWISSPIPE_ADAPTER (ou l'argument). Pur wiring."""
    nom = (nom or os.environ.get("SWISSPIPE_ADAPTER", "fake")).strip().lower()
    if nom == "fake":
        return AdaptateurMemoire()
    if nom == "nextcloud":
        return AdaptateurNextcloud("", "", "")  # SSH config lue par occ_runner (env/défauts)
    raise ConfigurationError(f"SWISSPIPE_ADAPTER inconnu : {nom!r} (attendu fake|nextcloud)")


def construire_sessionmaker(database_url: str | None = None) -> sessionmaker[Session]:
    """Engine + sessionmaker depuis DATABASE_URL. Aucune URL codée en dur."""
    url = database_url or os.environ.get("DATABASE_URL")
    if not url:
        raise ConfigurationError("DATABASE_URL non défini — requis pour joindre le cœur (Postgres)")
    return sessionmaker(bind=create_engine(url, future=True))


def fabrique_executeur_projection(
    session: Session, nom: str | None = None
) -> Callable[[uuid.UUID], ExecuteurProjection]:
    """Fabrique (montage_id -> ExecuteurProjection) pour le reconcile TRANSVERSE. Wiring pur.

    - fake      : exécuteur mémoire (hermétique, état volatile — dry-run/smoke).
    - nextcloud : ExecuteurProjectionOcc, GF résolu par mountPoint == montage.chemin_hote
      (la clé externe naturelle d'un transverse monté — prouvée étapes 7/8).
    """
    nom = (nom or os.environ.get("SWISSPIPE_ADAPTER", "fake")).strip().lower()

    if nom == "fake":
        memoire: dict[uuid.UUID, ExecuteurProjectionMemoire] = {}

        def _fake(montage_id: uuid.UUID) -> ExecuteurProjection:
            return memoire.setdefault(montage_id, ExecuteurProjectionMemoire())

        return _fake

    if nom == "nextcloud":

        def _reel(montage_id: uuid.UUID) -> ExecuteurProjection:
            montage = session.get(Montage, montage_id)
            if montage is None:
                raise ConfigurationError(f"montage {montage_id} introuvable")
            folders = json.loads(executer_occ(["groupfolders:list", "--output=json"]))
            items = list(folders.values()) if isinstance(folders, dict) else folders
            # FAIL-CLOSED : le ciblage doit être UNIVOQUE. Un doublon de mountPoint
            # (l'app groupfolders ne l'interdit pas) ciblerait un GF arbitraire et un
            # apply retirerait ses ACL — on refuse au lieu de prendre le 1er match.
            matches = [f for f in items if f.get("mountPoint") == montage.chemin_hote]
            if len(matches) > 1:
                ids = sorted(str(f["id"]) for f in matches)
                raise ConfigurationError(
                    f"mountPoint {montage.chemin_hote!r} ambigu : {len(matches)} Group "
                    f"Folders ({', '.join(ids)}) — ciblage refusé"
                )
            if not matches:
                raise ConfigurationError(
                    f"aucun Group Folder au mountPoint {montage.chemin_hote!r} — structure absente"
                )
            return ExecuteurProjectionOcc(str(matches[0]["id"]))

        return _reel

    raise ConfigurationError(f"SWISSPIPE_ADAPTER inconnu : {nom!r} (attendu fake|nextcloud)")


@contextmanager
def session_et_adaptateur(
    database_url: str | None = None, adaptateur: str | None = None
) -> Iterator[tuple[Session, AdaptateurRessource]]:
    """Contexte assemblé : (session, adaptateur). Ferme la session en sortie.

    L'appelant gère commit/rollback selon qu'il applique ou non (les services applicatifs
    commitent eux-mêmes pour les apply ; le dry-run ne commite jamais).
    """
    fabrique = construire_sessionmaker(database_url)
    adaptateur_obj = construire_adaptateur(adaptateur)
    session = fabrique()
    try:
        yield session, adaptateur_obj
    finally:
        session.close()
