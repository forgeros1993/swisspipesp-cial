"""Suite de CONTRAT paramétrée du port AdaptateurRessource (DOC 1, cas C1–C9).

Objectif : remplacer les 2 `isinstance` (preuve de FORME) par une preuve de
COMPORTEMENT. Les mêmes cas s'exécutent contre le fake (hermétique, toujours) ET
l'adaptateur Nextcloud réel (gaté par SWISSPIPE_NC_TEST=1 + SSH). Si les deux passent
les mêmes cas (hors divergences déclarées), l'agnosticité est démontrée.

Les cas n'utilisent QUE les 5 méthodes du port. Les opérations impossibles à faire « par
le port » (créer une ressource côté exécutant, injecter une dérive externe, nettoyer) sont
encapsulées dans un Harness par adaptateur — le test, lui, reste boîte noire.

Divergences LÉGITIMES déclarées en capability flags (jamais du masquage) :
- `rejette_groupe_vide` : INV-4 garanti PAR LE DTO `DroitGroupe` (garde `__post_init__`) ->
  fake ET NC lèvent désormais. Plus une divergence (la promesse est tenue en amont). cf. C9.
- `lecture_fidele_additionnels` : le NC ne reconstruit pas CLASSEMENT/TÉLÉCHARGEMENT
  depuis les bits Group Folder (perte documentée, traduction.py) ; le fake oui. cf. C6.
- `supporte_deny_sous_chemin` : REFUSER explicite sur un sous-chemin = L2 ; les deux ne
  le supportent pas en L1 (REFUSER racine = par-absence). cf. C8.

Garde-fous : écriture serveur UNIQUEMENT sur des Group Folders `zztest_<uuid>` (id > 20)
et groupes `zztest_grp_<uuid>`, supprimés en teardown. Jamais les dossiers prod (4–20).
"""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import Iterator

import pytest

from swisspipe.adapters.outbound.fake.adaptateur_memoire import AdaptateurMemoire
from swisspipe.adapters.outbound.nextcloud.adaptateur_nextcloud import AdaptateurNextcloud
from swisspipe.adapters.outbound.nextcloud.occ_runner import (
    NEXTCLOUD_SSH_ALIAS,
    OccError,
    executer_occ,
)
from swisspipe.core.domain.matrice import DroitAdditionnel, Matrice, NiveauPrincipal
from swisspipe.core.ports.adaptateur_ressource import (
    AdaptateurRessource,
    DescripteurRessource,
    DroitGroupe,
)

_ID_PROD_MAX = 20  # SÉCURITÉ : un folder de prod a un id <= 20 ; on n'opère que > 20.

LECTURE = Matrice(NiveauPrincipal.LECTURE)
ECRITURE = Matrice(NiveauPrincipal.ECRITURE)
SUPPRESSION = Matrice(NiveauPrincipal.SUPPRESSION)
# Matrice à additionnels non reconstructibles côté NC (round-trip lossy) :
ECRITURE_PLUS = Matrice(
    NiveauPrincipal.ECRITURE,
    {DroitAdditionnel.CLASSEMENT, DroitAdditionnel.TELECHARGEMENT},
)


# ───────────────────────── Harness par adaptateur ─────────────────────────
# Le Harness fournit ce que le PORT ne peut pas faire : matérialiser une ressource
# côté exécutant, fabriquer des groupes valides, injecter une dérive HORS port, nettoyer.
# Le test n'appelle que le port + ces helpers de mise en scène.


class HarnessFake:
    """Mise en scène hermétique pour l'adaptateur mémoire."""

    nom = "fake"
    caps = {
        "rejette_groupe_vide": True,  # INV-4 garanti par le DTO DroitGroupe (post-fix)
        "lecture_fidele_additionnels": True,  # stocke l'état exact
        "supporte_deny_sous_chemin": False,  # L2
    }

    def __init__(self) -> None:
        self.adaptateur: AdaptateurRessource = AdaptateurMemoire()
        self._groupes = [f"grp_{uuid.uuid4().hex[:8]}" for _ in range(2)]

    def setup(self) -> None:  # rien à provisionner
        pass

    def teardown(self) -> None:  # rien à nettoyer
        pass

    def creer(self, nom: str) -> str:
        return self.adaptateur.creer_ressource(
            DescripteurRessource(type="folder", chemin="/", nom=nom)
        )

    def groupe(self, i: int = 0) -> str:
        return self._groupes[i]

    def injecter_derive_clear(self, cle: str, _groupe: str) -> None:
        # Dérive HORS port : on vide l'état interne (équivaut à une règle effacée).
        self.adaptateur._ressources[cle].droits = frozenset()  # type: ignore[attr-defined]


class HarnessNextcloud:
    """Mise en scène contre le VRAI Nextcloud, sur ressources jetables `zztest_`."""

    nom = "nextcloud"
    caps = {
        "rejette_groupe_vide": True,  # appliquer_droits lève sur groupe_id vide (INV-4)
        "lecture_fidele_additionnels": False,  # CLASSEMENT/TÉLÉCHARGEMENT non relus depuis bits
        "supporte_deny_sous_chemin": False,  # L2 (deny racine = par-absence seulement)
    }

    def __init__(self) -> None:
        self.adaptateur: AdaptateurRessource = AdaptateurNextcloud("", "", "")
        self._groupes = [f"zztest_grp_{uuid.uuid4().hex[:8]}" for _ in range(2)]
        self._cles: list[str] = []

    def setup(self) -> None:
        for grp in self._groupes:
            executer_occ(["group:add", grp])

    def teardown(self) -> None:
        for cle in self._cles:
            try:
                executer_occ(["groupfolders:delete", str(cle), "--force"])
            except OccError:
                pass  # best-effort
        for grp in self._groupes:
            try:
                executer_occ(["group:delete", grp])
            except OccError:
                pass

    def creer(self, nom: str) -> str:
        cle = self.adaptateur.creer_ressource(
            DescripteurRessource(type="folder", chemin="/", nom=f"zztest_{nom}_{uuid.uuid4().hex[:6]}")
        )
        assert int(cle) > _ID_PROD_MAX, f"SÉCURITÉ : id {cle} <= {_ID_PROD_MAX} (prod !) — refus"
        self._cles.append(cle)
        return cle

    def groupe(self, i: int = 0) -> str:
        return self._groupes[i]

    def injecter_derive_clear(self, cle: str, groupe: str) -> None:
        # Dérive HORS port : effacer la règle ACL du groupe directement via occ.
        executer_occ(["groupfolders:permissions", str(cle), "/", "-g", groupe, "--", "clear"])


# ───────────────────────── Sélection des adaptateurs ─────────────────────────


def _nc_active() -> bool:
    """NC activé si SWISSPIPE_NC_TEST=1 ET serveur SSH joignable (sinon skip propre)."""
    if os.environ.get("SWISSPIPE_NC_TEST") != "1":
        return False
    try:
        proc = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", NEXTCLOUD_SSH_ALIAS, "true"],
            capture_output=True,
            timeout=15,
        )
        return proc.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


_HARNESSES: list[type] = [HarnessFake] + ([HarnessNextcloud] if _nc_active() else [])


@pytest.fixture(params=_HARNESSES, ids=lambda h: h.nom)
def harness(request: pytest.FixtureRequest) -> Iterator[HarnessFake | HarnessNextcloud]:
    h = request.param()
    h.setup()
    try:
        yield h
    finally:
        h.teardown()


# ───────────────────────── Cas de contrat C1–C9 ─────────────────────────


def test_c1_creer_ressource_existe(harness) -> None:
    """C1 — creer_ressource retourne une clé ; la ressource existe ensuite.

    Indépendant de l'impl : on crée par le port, puis lire_droits_effectifs ne lève pas.
    """
    cle = harness.creer("c1")
    assert isinstance(cle, str) and cle
    assert harness.adaptateur.lire_droits_effectifs(cle) == frozenset()  # créée, sans droits


def test_c2_archiver_reversible_jamais_delete(harness) -> None:
    """C2 — archiver = réversible, JAMAIS suppression dure (INV-5).

    Après archivage la ressource EXISTE encore (lire ne lève pas) et on peut ré-appliquer
    des droits (réversible). On n'assert PAS l'égalité des droits (le NC retire les groupes,
    le fake garde un flag — divergence d'impl légitime) ; on assert la NON-destruction.
    """
    cle = harness.creer("c2")
    grp = harness.groupe()
    harness.adaptateur.appliquer_droits(cle, {DroitGroupe(grp, ECRITURE)})

    harness.adaptateur.archiver_ressource(cle)
    # Non détruit : lire fonctionne toujours (pas de KeyError / ressource absente).
    harness.adaptateur.lire_droits_effectifs(cle)

    # Réversible : ré-appliquer redonne l'accès.
    harness.adaptateur.appliquer_droits(cle, {DroitGroupe(grp, ECRITURE)})
    assert DroitGroupe(grp, ECRITURE) in harness.adaptateur.lire_droits_effectifs(cle)


def test_c3_renommer(harness) -> None:
    """C3 — renommer change le nom externe ; clé et droits inchangés."""
    cle = harness.creer("c3")
    grp = harness.groupe()
    harness.adaptateur.appliquer_droits(cle, {DroitGroupe(grp, ECRITURE)})
    avant = harness.adaptateur.lire_droits_effectifs(cle)

    harness.adaptateur.renommer_ressource(cle, f"zztest_renomme_{uuid.uuid4().hex[:6]}")

    assert harness.adaptateur.lire_droits_effectifs(cle) == avant  # droits intacts


def test_c4_appliquer_droits_idempotent(harness) -> None:
    """C4 — appliquer 2× le même état désiré ⇒ lire renvoie le même résultat (idempotent)."""
    cle = harness.creer("c4")
    etat = {DroitGroupe(harness.groupe(), ECRITURE)}

    harness.adaptateur.appliquer_droits(cle, etat)
    r1 = harness.adaptateur.lire_droits_effectifs(cle)
    harness.adaptateur.appliquer_droits(cle, etat)
    r2 = harness.adaptateur.lire_droits_effectifs(cle)

    assert r1 == r2  # pas de double, pas de dérive


def test_c5_appliquer_droits_etat_complet(harness) -> None:
    """C5 — l'état désiré est COMPLET (pas un diff) : un groupe retiré DISPARAÎT.

    Invariant clé du port. On pose {A, B} puis {A} ; B doit être absent côté exécutant.
    """
    cle = harness.creer("c5")
    a, b = harness.groupe(0), harness.groupe(1)

    harness.adaptateur.appliquer_droits(cle, {DroitGroupe(a, ECRITURE), DroitGroupe(b, LECTURE)})
    apres_deux = {d.groupe_id for d in harness.adaptateur.lire_droits_effectifs(cle)}
    assert a in apres_deux and b in apres_deux

    harness.adaptateur.appliquer_droits(cle, {DroitGroupe(a, ECRITURE)})
    apres_un = {d.groupe_id for d in harness.adaptateur.lire_droits_effectifs(cle)}
    assert a in apres_un
    assert b not in apres_un  # B retiré de l'état désiré ⇒ retiré côté exécutant


def test_c6_lire_reflete_applique(harness) -> None:
    """C6 — round-trip : lire == appliqué.

    Niveau principal toujours fidèle. Les additionnels CLASSEMENT/TÉLÉCHARGEMENT ne sont
    pas reconstructibles côté NC (capability `lecture_fidele_additionnels=False`) : pour ces
    adaptateurs on ne compare que le NIVEAU (le comparable), divergence DÉCLARÉE non masquée.
    """
    cle = harness.creer("c6")
    grp = harness.groupe()
    etat = {DroitGroupe(grp, ECRITURE_PLUS)}
    harness.adaptateur.appliquer_droits(cle, etat)
    lu = harness.adaptateur.lire_droits_effectifs(cle)

    if harness.caps["lecture_fidele_additionnels"]:
        assert lu == frozenset(etat)  # exact (fake)
    else:
        # NC : niveau fidèle, additionnels lossy (documenté) — on compare le comparable.
        niveaux = {(d.groupe_id, d.matrice.niveau) for d in lu}
        assert (grp, NiveauPrincipal.ECRITURE) in niveaux


def test_c7_reconciliation_detecte_et_corrige(harness) -> None:
    """C7 — dérive introduite HORS port ⇒ lire la DÉTECTE ⇒ ré-appliquer corrige."""
    cle = harness.creer("c7")
    grp = harness.groupe()
    etat = {DroitGroupe(grp, ECRITURE)}

    harness.adaptateur.appliquer_droits(cle, etat)
    assert harness.adaptateur.lire_droits_effectifs(cle) == frozenset(etat)

    harness.injecter_derive_clear(cle, grp)  # mutation externe (hors cœur)
    assert harness.adaptateur.lire_droits_effectifs(cle) != frozenset(etat)  # dérive détectée

    harness.adaptateur.appliquer_droits(cle, etat)  # réconciliation
    assert harness.adaptateur.lire_droits_effectifs(cle) == frozenset(etat)  # corrigée


def test_c8_refuser_par_absence(harness) -> None:
    """C8 — REFUSER racine = deny-par-absence : un groupe non désiré n'a aucun droit.

    En L1, REFUSER se réalise en RETIRANT le groupe de l'état désiré (cf. CLAUDE.md §8).
    Le deny explicite SUR SOUS-CHEMIN est L2 (capability `supporte_deny_sous_chemin`).
    """
    cle = harness.creer("c8")
    grp = harness.groupe()

    harness.adaptateur.appliquer_droits(cle, {DroitGroupe(grp, ECRITURE)})
    assert grp in {d.groupe_id for d in harness.adaptateur.lire_droits_effectifs(cle)}

    # REFUSER résolu côté cœur ⇒ groupe absent de l'état désiré ⇒ accès retiré.
    harness.adaptateur.appliquer_droits(cle, frozenset())
    assert grp not in {d.groupe_id for d in harness.adaptateur.lire_droits_effectifs(cle)}

    if not harness.caps["supporte_deny_sous_chemin"]:
        pytest.skip(f"[{harness.nom}] deny explicite sous-chemin = L2 (non implémenté)")


def test_c9_inv4_structurel(harness) -> None:
    """C9 (structurel) — le contrat ne peut PAS nommer une personne : DroitGroupe ne porte
    qu'un groupe_id + une matrice (aucun champ user). Vrai pour TOUT adaptateur."""
    champs = set(DroitGroupe.__dataclass_fields__)
    assert champs == {"groupe_id", "matrice"}  # pas de 'user'/'uid' : INV-4 structurel


def test_c9_inv4_groupe_vide_rejete(harness) -> None:
    """C9 (garde) — groupe_id vide rejeté.

    INV-4 désormais garanti par le DTO `DroitGroupe` (post-fix) : la garde fire à la
    CONSTRUCTION du DroitGroupe, donc fake ET NC lèvent identiquement. Plus une divergence.
    """
    cle = harness.creer("c9")
    if not harness.caps["rejette_groupe_vide"]:
        pytest.skip(f"[{harness.nom}] capability rejette_groupe_vide=False")
    with pytest.raises((ValueError, OccError)):
        # Le ValueError part dès la construction de DroitGroupe("", …) (garde DTO INV-4).
        harness.adaptateur.appliquer_droits(cle, {DroitGroupe("", ECRITURE)})


# ───────────────────── Test direct du DTO (INV-4 au contrat) ─────────────────────


def test_dto_droitgroupe_refuse_groupe_vide() -> None:
    """Le DTO refuse un groupe_id vide ou blanc à la construction (INV-4, hermétique)."""
    with pytest.raises(ValueError, match="INV-4"):
        DroitGroupe("", ECRITURE)
    with pytest.raises(ValueError, match="INV-4"):
        DroitGroupe("   ", ECRITURE)


def test_dto_droitgroupe_accepte_groupe_valide() -> None:
    """Un groupe_id non vide construit un DroitGroupe normal."""
    dg = DroitGroupe("grp_valide", ECRITURE)
    assert dg.groupe_id == "grp_valide"
    assert dg.matrice == ECRITURE
