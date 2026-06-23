"""CLI d'exploitation SwissPipe — inbound. WIRING + parse + appel des services existants.

ZÉRO logique métier : chaque commande assemble (composition root) puis appelle un service
applicatif (diagnostiquer_* en lecture, reconcilier_* en écriture) et formate la sortie.

Sûreté de la bascule (DOC 2) : tout ce qui ÉCRIT exige `--apply` explicite. Sans `--apply`,
les commandes sont en DRY-RUN (montrent ce qu'elles FERAIENT, n'écrivent rien). Le cron est
SHADOW par défaut + advisory lock anti-concurrence.

Codes de sortie : 0 = conforme/appliqué ; 2 = divergence détectée (dry-run/shadow) ;
1 = erreur ; 0 + log si cron déjà en cours (skip bénin).
"""

from __future__ import annotations

import argparse
import sys
import uuid
from collections.abc import Sequence

from sqlalchemy import text
from sqlalchemy.orm import Session

from swisspipe.adapters.inbound.composition import ConfigurationError, session_et_adaptateur
from swisspipe.application.reconciliation_service import (
    RapportReconciliation,
    ResultatRessource,
    diagnostiquer_ressource,
    diagnostiquer_tout,
    reconcilier_ressource,
    reconcilier_tout,
)
from swisspipe.core.ports.adaptateur_ressource import AdaptateurRessource
from swisspipe.core.services.reconciliation import Divergence

# Clé d'advisory lock (constante arbitraire stable) pour sérialiser les runs cron.
_CRON_LOCK_KEY = 0x5752_4543  # "SREC"

EXIT_OK = 0
EXIT_ERREUR = 1
EXIT_DIVERGENCE = 2


# ───────────────────────── Formatage (pas de logique) ─────────────────────────


def _fmt_divergence(rid: uuid.UUID, div: Divergence) -> str:
    bouts = []
    if div.groupes_manquants:
        bouts.append(f"manquants={[d.groupe_id for d in div.groupes_manquants]}")
    if div.groupes_en_trop:
        bouts.append(f"en_trop={[d.groupe_id for d in div.groupes_en_trop]}")
    if div.matrices_divergentes:
        bouts.append(f"matrices_divergentes={[m.groupe_id for m in div.matrices_divergentes]}")
    return f"  - {rid} : {', '.join(bouts) or 'divergent'}"


def _rendre(rapport: RapportReconciliation, *, applique: bool) -> int:
    """Affiche le rapport, renvoie le code de sortie. Aucune décision métier."""
    divergents = [r for r in rapport.resultats if r.statut in ("diverge", "reparee")]
    erreurs = rapport.ressources_en_erreur
    verbe = "réparées" if applique else "divergentes (DRY-RUN, rien écrit)"
    print(
        f"ressources={rapport.total} conformes={rapport.nb_conformes} "
        f"{verbe}={len(divergents)} erreurs={len(erreurs)}"
    )
    for r in divergents:
        if r.divergence is not None:
            print(_fmt_divergence(r.ressource_id, r.divergence))
    for r in erreurs:
        print(f"  ! {r.ressource_id} : {r.erreur}")
    if erreurs:
        return EXIT_ERREUR
    if divergents and not applique:
        return EXIT_DIVERGENCE  # exploitable par le monitoring/cron
    return EXIT_OK


# ───────────────────────── Commandes ─────────────────────────


def _cmd_verifier(
    session: Session, adaptateur: AdaptateurRessource, _args: argparse.Namespace
) -> int:
    """DRY-RUN pur : liste les divergences, n'écrit jamais (phase shadow T3)."""
    return _rendre(diagnostiquer_tout(session, adaptateur), applique=False)


def _cmd_reconcilier(
    session: Session, adaptateur: AdaptateurRessource, args: argparse.Namespace
) -> int:
    """Réconcilie. DRY-RUN par défaut ; n'écrit que si --apply. Cible une ressource ou tout."""
    rid = uuid.UUID(args.ressource) if args.ressource else None
    declencheur = f"cli:{args.acteur}" if args.acteur else "cli"

    if not args.apply:  # dry-run explicite : montrer ce qui SERAIT fait
        if rid is not None:
            div = diagnostiquer_ressource(session, adaptateur, rid)
            rapport = RapportReconciliation(
                (_resultat_dry(rid, div),)
            )
        else:
            rapport = diagnostiquer_tout(session, adaptateur)
        return _rendre(rapport, applique=False)

    # --apply : écrire réellement (les services commitent + journalisent avec declencheur)
    if rid is not None:
        div = reconcilier_ressource(session, adaptateur, rid, declencheur=declencheur)
        session.commit()
        rapport = RapportReconciliation((_resultat_applique(rid, div),))
    else:
        rapport = reconcilier_tout(session, adaptateur, declencheur=declencheur)
    return _rendre(rapport, applique=True)


def _cmd_cron(
    session: Session, adaptateur: AdaptateurRessource, args: argparse.Namespace
) -> int:
    """Cron : SHADOW par défaut (détecte+loggue, n'écrit rien). --apply pour réparer.

    Advisory lock Postgres : si un run tient déjà le lock, on s'abstient proprement (pas de
    réconciliation concurrente). Stamp declencheur='cron' pour le journal (si --apply).
    """
    verrou = session.execute(
        text("SELECT pg_try_advisory_lock(:k)"), {"k": _CRON_LOCK_KEY}
    ).scalar()
    if not verrou:
        print("[cron] un run est déjà en cours (advisory lock tenu) — skip")
        return EXIT_OK
    try:
        if not args.apply:
            print("[cron] mode SHADOW (dry-run) — aucune écriture")
            return _rendre(diagnostiquer_tout(session, adaptateur), applique=False)
        print("[cron] mode APPLY — réparation + journal (declencheur=cron)")
        rapport = reconcilier_tout(session, adaptateur, declencheur="cron")
        return _rendre(rapport, applique=True)
    finally:
        session.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": _CRON_LOCK_KEY})


# ───────────────────────── Helpers de rapport (formatage) ─────────────────────────


def _resultat_dry(rid: uuid.UUID, div: Divergence) -> ResultatRessource:
    return ResultatRessource(rid, "conforme" if div.est_conforme else "diverge", divergence=div)


def _resultat_applique(rid: uuid.UUID, div: Divergence) -> ResultatRessource:
    return ResultatRessource(rid, "conforme" if div.est_conforme else "reparee", divergence=div)


# ───────────────────────── Entrée ─────────────────────────


def construire_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="swisspipe", description="Exploitation SwissPipe (inbound)")
    p.add_argument("--adapter", help="fake|nextcloud (défaut env SWISSPIPE_ADAPTER)")
    p.add_argument("--database-url", help="URL Postgres du cœur (défaut env DATABASE_URL)")
    sub = p.add_subparsers(dest="commande", required=True)

    sub.add_parser("verifier", help="DRY-RUN : liste les divergences, n'écrit rien")

    r = sub.add_parser("reconcilier", help="Réconcilie (DRY-RUN sauf --apply)")
    r.add_argument("--ressource", help="UUID d'une ressource (défaut : toutes)")
    r.add_argument("--apply", action="store_true", help="ÉCRIRE réellement (opt-in)")
    r.add_argument("--acteur", help="opérateur (journalisé via declencheur)")

    c = sub.add_parser("cron-run", help="Run cron : SHADOW par défaut, --apply pour réparer")
    c.add_argument("--apply", action="store_true", help="ÉCRIRE réellement (opt-in)")
    return p


_COMMANDES = {"verifier": _cmd_verifier, "reconcilier": _cmd_reconcilier, "cron-run": _cmd_cron}


def main(argv: Sequence[str] | None = None) -> int:
    args = construire_parser().parse_args(argv)
    try:
        with session_et_adaptateur(args.database_url, args.adapter) as (session, adaptateur):
            return _COMMANDES[args.commande](session, adaptateur, args)
    except ConfigurationError as e:
        print(f"erreur de configuration : {e}", file=sys.stderr)
        return EXIT_ERREUR


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
