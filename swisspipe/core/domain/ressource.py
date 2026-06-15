"""Entité Ressource — l'objet gouverné, clé de l'agnosticité (spec §3.3, §4.6).

100% stdlib, frozen dataclasses, immuables. Aucun import externe (garde-fou de
pureté : swisspipe/tests/test_core_purity.py, CLAUDE.md §1/§5).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Ressource:
    """Objet gouverné par le cœur (spec §3.3, §4.6). Identité = `id`.

    AGNOSTICITÉ — POINT CRITIQUE (§3.3), à comprendre avant toute modification :
    la Ressource NE porte JAMAIS son identifiant externe (chemin WebDAV, id de
    boîte mail, id de porte, etc.). Le cœur ne connaît QUE l'`id` interne (opaque,
    propriété de SwissPipe) et le `type`. Le mapping interne↔externe vit dans une
    table SÉPARÉE `ressource_mapping`, gérée par chaque adaptateur (Nextcloud, mail,
    bâtiment). C'est cette indirection qui permet de remplacer Nextcloud sans
    toucher au modèle de droits. N'AJOUTEZ donc aucun champ d'identifiant externe
    ici (un test d'agnosticité échoue exprès si on le fait).

    `type` est une chaîne LIBRE et extensible (folder, mailbox, door, …) — pas un
    enum fermé : un futur adaptateur bâtiment ajoute "door" sans toucher au cœur.

    Champs hors identité (`type`, `chemin`, `espace_id`) : exclus de l'égalité et du
    hash — deux ressources de même `id` sont la même ressource.
    """

    id: str
    type: str = field(compare=False)
    chemin: str = field(compare=False)
    espace_id: str = field(compare=False)
