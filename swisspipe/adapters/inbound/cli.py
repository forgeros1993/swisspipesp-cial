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
from collections.abc import Callable, Sequence

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from swisspipe.adapters.inbound.composition import (
    ConfigurationError,
    fabrique_executeur_projection,
    session_et_adaptateur,
)
from swisspipe.application.projection_service import (
    ExecuteurProjection,
    RapportProjection,
    reconcilier_projection,
)
from swisspipe.application.reconciliation_service import (
    RapportReconciliation,
    ResultatRessource,
    diagnostiquer_ressource,
    diagnostiquer_tout,
    reconcilier_ressource,
    reconcilier_tout,
)
from swisspipe.core.domain.montage import EtatMontage
from swisspipe.core.ports.adaptateur_ressource import AdaptateurRessource
from swisspipe.core.services.reconciliation import Divergence
from swisspipe.persistence.models import Montage

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
            rapport = RapportReconciliation((_resultat_dry(rid, div),))
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


def _cmd_cron(session: Session, adaptateur: AdaptateurRessource, args: argparse.Namespace) -> int:
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


# ───────────────────────── Transverse (étape 9) : reconcile de projection ─────────────


def _montages_actifs(session: Session) -> list[uuid.UUID]:
    """Ids des montages ACTIFS (les archivés sont EXCLUS du balayage ; leur retrait est
    une opération explicite : reconcilier-transverse --montage <id>). Ordre déterministe."""
    return list(
        session.scalars(
            select(Montage.id).where(Montage.etat == EtatMontage.ACTIF).order_by(Montage.id)
        ).all()
    )


def _fmt_delta(montage_id: uuid.UUID, rapport: RapportProjection) -> str:
    d = rapport.delta
    bouts = []
    if d.a_creer:
        bouts.append(f"a_creer={sorted(d.a_creer)}")
    if d.a_modifier:
        bouts.append(f"a_modifier={sorted(d.a_modifier)}")
    if d.a_retirer:
        bouts.append(f"a_retirer={sorted(d.a_retirer)}")
    for perte in rapport.droits_non_projetables:
        bouts.append(f"NON-PROJETABLE {perte.ressource}/{perte.groupe}: {list(perte.additionnels)}")
    return f"  - montage {montage_id} : {', '.join(bouts) or 'conforme'}"


def _reconcilier_transverses(
    session: Session,
    args: argparse.Namespace,
    fabrique_executeur: Callable[[uuid.UUID], ExecuteurProjection],
) -> int:
    """Boucle commune : un montage ciblé ou tous les actifs. Résilient (une erreur
    n'arrête pas le balayage, moule reconcilier_tout). SHADOW par défaut ; --apply
    exécute le delta + commit par montage (journal projection_partielle éventuel)."""
    # getattr : cron-transverse n'a pas d'option --montage (balayage des actifs seulement).
    montage_arg = getattr(args, "montage", None)
    if montage_arg:
        try:
            ids = [uuid.UUID(str(montage_arg))]
        except ValueError:
            print(f"erreur : --montage {montage_arg!r} n'est pas un UUID valide")
            return EXIT_ERREUR
    else:
        ids = _montages_actifs(session)
    if not args.apply:
        print(f"[transverse] mode SHADOW (dry-run) — aucune écriture ({len(ids)} montage(s))")
    divergents = 0
    erreurs = 0
    for montage_id in ids:
        try:
            rapport = reconcilier_projection(
                session, montage_id, executeur=fabrique_executeur(montage_id), apply=args.apply
            )
            if args.apply:
                session.commit()
            if not rapport.delta.est_vide or rapport.droits_non_projetables:
                print(_fmt_delta(montage_id, rapport))
            if not rapport.delta.est_vide:
                divergents += 1
        except Exception as e:  # résilient : on continue le balayage
            session.rollback()
            erreurs += 1
            print(f"  ! montage {montage_id} : {type(e).__name__}: {e}")
    print(
        f"montages={len(ids)} divergents={divergents} erreurs={erreurs} "
        f"({'APPLY' if args.apply else 'SHADOW, rien écrit'})"
    )
    if erreurs:
        return EXIT_ERREUR
    if divergents and not args.apply:
        return EXIT_DIVERGENCE  # exploitable par le monitoring/cron
    return EXIT_OK


def _cmd_reconcilier_transverse(
    session: Session,
    args: argparse.Namespace,
    fabrique_executeur: Callable[[uuid.UUID], ExecuteurProjection],
) -> int:
    """Reconcile transverse : UN montage (--montage) ou TOUS les actifs. SHADOW sauf --apply."""
    return _reconcilier_transverses(session, args, fabrique_executeur)


# Clé d'advisory lock dédiée au cron transverse ("TREC"), distincte du cron dimensionnel.
_CRON_TRANSVERSE_LOCK_KEY = 0x5452_4543


def _cmd_cron_transverse(
    session: Session,
    args: argparse.Namespace,
    fabrique_executeur: Callable[[uuid.UUID], ExecuteurProjection],
) -> int:
    """Cron transverse : SHADOW par défaut (surveillance périodique). --apply pour réparer.

    Advisory lock Postgres dédié : pas de reconcile transverse concurrent.
    """
    verrou = session.execute(
        text("SELECT pg_try_advisory_lock(:k)"), {"k": _CRON_TRANSVERSE_LOCK_KEY}
    ).scalar()
    if not verrou:
        print("[cron-transverse] un run est déjà en cours (advisory lock tenu) — skip")
        return EXIT_OK
    try:
        print(f"[cron-transverse] mode {'APPLY' if args.apply else 'SHADOW'}")
        return _reconcilier_transverses(session, args, fabrique_executeur)
    finally:
        session.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": _CRON_TRANSVERSE_LOCK_KEY})


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

    rt = sub.add_parser(
        "reconcilier-transverse",
        help="Reconcile la projection des transverses (SHADOW sauf --apply)",
    )
    rt.add_argument("--montage", help="UUID d'un montage (défaut : tous les ACTIFS)")
    rt.add_argument("--apply", action="store_true", help="ÉCRIRE réellement (opt-in)")

    ct = sub.add_parser(
        "cron-transverse",
        help="Cron transverse : SHADOW par défaut (surveillance), --apply pour réparer",
    )
    ct.add_argument("--apply", action="store_true", help="ÉCRIRE réellement (opt-in)")
    return p


_COMMANDES = {"verifier": _cmd_verifier, "reconcilier": _cmd_reconcilier, "cron-run": _cmd_cron}
_COMMANDES_TRANSVERSE = {
    "reconcilier-transverse": _cmd_reconcilier_transverse,
    "cron-transverse": _cmd_cron_transverse,
}


def main(argv: Sequence[str] | None = None) -> int:
    args = construire_parser().parse_args(argv)
    try:
        with session_et_adaptateur(args.database_url, args.adapter) as (session, adaptateur):
            if args.commande in _COMMANDES_TRANSVERSE:
                fabrique = fabrique_executeur_projection(session, args.adapter)
                return _COMMANDES_TRANSVERSE[args.commande](session, args, fabrique)
            return _COMMANDES[args.commande](session, adaptateur, args)
    except ConfigurationError as e:
        print(f"erreur de configuration : {e}", file=sys.stderr)
        return EXIT_ERREUR


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
