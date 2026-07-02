"""Traduction matrice abstraite -> masque de permissions Nextcloud (Group Folders).

DÉCISION D'ADAPTATEUR (§3.2), pas du domaine : ce mapping n'a AUCUNE autorité sur le
cœur. Le cœur reste agnostique ; c'est ici, dans l'adaptateur, qu'on choisit comment
projeter une Matrice sur les bits Nextcloud. Aucun appel réseau (100% testable).

Bits de permission Nextcloud (app Group Folders) :
    read=1, update=2, create=4, delete=8, share=16  (31 = somme des cinq)

Mapping retenu :
- NiveauPrincipal.LECTURE      -> read                     = 1
- NiveauPrincipal.ECRITURE     -> read|update              = 3   (modifier l'existant)
- NiveauPrincipal.SUPPRESSION  -> read|update|delete       = 11
- DroitAdditionnel.CREATION    -> + create (4)
- DroitAdditionnel.CLASSEMENT  -> + create|delete (déplacer = create@dest + delete@source)
- DroitAdditionnel.TELECHARGEMENT -> AUCUN bit (voir question ouverte ci-dessous)

`share` (16) n'est jamais octroyé par cette traduction (le partage n'est pas un droit
de notre matrice).

QUESTIONS OUVERTES (à ne PAS figer sans validation) :
- TÉLÉCHARGEMENT : Nextcloud Group Folders n'a pas de bit "download" distinct. Le
  download suit `read` ; interdire le download relève de `files_accesscontrol` /
  paramètres de partage, pas des permission-bits. -> NON mappable ici, contribue 0 bit.
  À traiter via files_accesscontrol dans une tranche ultérieure. (On n'invente pas de
  faux bit.)
- CLASSEMENT : "classer/ranger" = déplacer. Un move WebDAV = create (destination) +
  delete (source), d'où create|delete. MAIS cela SUR-OCTROIE un `delete` brut (capacité
  de suppression de fichiers) au-delà de l'intention "ranger". Interprétation de travail,
  **À CONFIRMER** — peut-être à restreindre via files_accesscontrol plus tard.
"""

from __future__ import annotations

from swisspipe.core.domain.matrice import DroitAdditionnel, Matrice, NiveauPrincipal

# Bits Nextcloud (Group Folders).
PERM_READ = 1
PERM_UPDATE = 2
PERM_CREATE = 4
PERM_DELETE = 8
PERM_SHARE = 16
PERM_ALL = 31

_NIVEAU_VERS_BITS: dict[NiveauPrincipal, int] = {
    NiveauPrincipal.LECTURE: PERM_READ,
    NiveauPrincipal.ECRITURE: PERM_READ | PERM_UPDATE,
    NiveauPrincipal.SUPPRESSION: PERM_READ | PERM_UPDATE | PERM_DELETE,
}

_ADDITIONNEL_VERS_BITS: dict[DroitAdditionnel, int] = {
    DroitAdditionnel.CREATION: PERM_CREATE,
    # Déplacer = create@dest + delete@source (sur-octroi de delete : À CONFIRMER).
    DroitAdditionnel.CLASSEMENT: PERM_CREATE | PERM_DELETE,
    # TELECHARGEMENT : volontairement absent (non mappable en bits, cf. docstring).
}


def matrice_vers_permissions_nextcloud(matrice: Matrice) -> int:
    """Projette une Matrice du domaine sur le masque de bits Nextcloud.

    Déterministe et pur. `TELECHARGEMENT` n'ajoute aucun bit (question ouverte).
    """
    bits = _NIVEAU_VERS_BITS[matrice.niveau]
    for additionnel in matrice.additionnels:
        bits |= _ADDITIONNEL_VERS_BITS.get(additionnel, 0)
    return bits


def permissions_nextcloud_vers_matrice(masque: int) -> Matrice | None:
    """Sens INVERSE : masque de bits Nextcloud -> Matrice abstraite.

    Décision d'adaptateur (§3.2), validée. Sert à la RÉCONCILIATION (relire l'état réel
    côté exécutant), PAS de source de vérité — la source reste le cœur. La traduction
    aller n'étant pas bijective, ce retour est volontairement PARTIEL :

    - niveau : bit delete(8) -> SUPPRESSION ; sinon update(2) -> ÉCRITURE ;
      sinon read(1) -> LECTURE ; sinon (pas de read) -> None (aucun droit).
    - bit create(4) -> additionnel CRÉATION (récupérable).
    - bit share(16) -> IGNORÉ (hors matrice ; le partage n'est jamais octroyé).
    - CLASSEMENT et TÉLÉCHARGEMENT -> NON reconstructibles depuis les bits (perte
      assumée, cohérente avec Q-téléchargement et D7). On compare donc le comparable.
    """
    if not (masque & PERM_READ):
        return None

    if masque & PERM_DELETE:
        niveau = NiveauPrincipal.SUPPRESSION
    elif masque & PERM_UPDATE:
        niveau = NiveauPrincipal.ECRITURE
    else:
        niveau = NiveauPrincipal.LECTURE

    additionnels: set[DroitAdditionnel] = set()
    if masque & PERM_CREATE:
        additionnels.add(DroitAdditionnel.CREATION)

    return Matrice(niveau, frozenset(additionnels))


def matrice_vers_verbes_acl(matrice: Matrice) -> list[str]:
    """Matrice -> liste de verbes ACL gouvernant les 5 verbes (`+verb`/`-verb`).

    Gouverne TOUS les verbes explicitement (read/write/create/delete/share) pour que le
    round-trip appliquer->lire soit symétrique (regle_acl_vers_matrice ne lit que les
    bits gouvernés). Ordre déterministe : read, write, create, delete, share.

    - read : toujours `+read` (toute matrice a au moins LECTURE).
    - write (= update) : `+write` si niveau >= ÉCRITURE, sinon `-write`.
    - delete : `+delete` si niveau == SUPPRESSION, sinon `-delete`.
    - create : `+create` si additionnel CRÉATION, sinon `-create`.
    - share : toujours `-share` (jamais octroyé).

    CLASSEMENT / TÉLÉCHARGEMENT : pas de verbe ACL dédié (mapping non bijectif déjà
    documenté) -> non traduits ici.
    """
    a_write = matrice.niveau.rang >= NiveauPrincipal.ECRITURE.rang
    a_delete = matrice.niveau is NiveauPrincipal.SUPPRESSION
    a_create = DroitAdditionnel.CREATION in matrice.additionnels

    def verbe(nom: str, present: bool) -> str:
        return ("+" if present else "-") + nom

    return [
        verbe("read", True),
        verbe("write", a_write),
        verbe("create", a_create),
        verbe("delete", a_delete),
        verbe("share", False),
    ]


def regle_acl_vers_matrice(mask: int, permissions: int) -> Matrice | None:
    """Règle ACL Group Folders (`mask` + `permissions`) -> Matrice abstraite.

    Modèle ACL (table group_folders_acl) : `mask` = bits GOUVERNÉS par la règle
    (override) ; `permissions` = valeurs pour ces bits. Un bit gouverné ET autorisé =
    `+verb` ; gouverné ET clear = `-verb` (deny) ; hors mask = hérité.

    On ne lit que les bits GOUVERNÉS-ET-AUTORISÉS (`mask & permissions`) et on réutilise
    le décodage du sens inverse. Conséquences :
    - `read` gouverné et refusé (deny) -> aucun bit read autorisé -> None = REFUSER
      (le groupe est omis du frozenset, cohérent avec un masque sans read).
    - CLASSEMENT/TÉLÉCHARGEMENT non reconstructibles (cf. permissions_nextcloud_vers_matrice).

    Limite assumée : les bits HÉRITÉS (∉ mask) ne sont pas interprétés. Nos propres
    écritures (appliquer_droits, C2) gouverneront TOUS les verbes, donc le round-trip
    racine reste symétrique ; une règle tierce partielle serait lue sur ses seuls bits
    gouvernés.
    """
    return permissions_nextcloud_vers_matrice(mask & permissions)


def matrice_projetable(matrice: Matrice) -> Matrice:
    """Réduit une Matrice à son sous-ensemble PROJETABLE en verbes ACL (le comparable).

    CLASSEMENT et TÉLÉCHARGEMENT n'ont aucun verbe ACL (mapping non bijectif documenté
    ci-dessus) : une matrice qui les porte, une fois posée puis relue, revient SANS eux.
    Pour que le reconcile soit idempotent (désiré == relu), le désiré doit être comparé
    sur ce sous-ensemble : niveau + CRÉATION uniquement. Pur.
    """
    return Matrice(matrice.niveau, matrice.additionnels & {DroitAdditionnel.CREATION})
