"""Ceinture (en plus des bretelles ruff) : pureté du cœur, vérifiée par AST.

Parcourt tous les .py de swisspipe/core/, parse les imports avec `ast`, et échoue
si un import interdit est présent. Indépendant de ruff : ce test casse même si
quelqu'un désactive la règle TID251.

Politique (cf. CLAUDE.md §1/§5) :
- core/**       : interdit d'importer une lib d'infrastructure (sqlalchemy, fastapi,
                  starlette, uvicorn, alembic, psycopg) et les couches
                  swisspipe.adapters.* / swisspipe.persistence.*.
- core/domain/** : interdit EN PLUS pydantic (domaine 100% stdlib).
                  pydantic reste autorisé dans core/services.
"""

from __future__ import annotations

import ast
from pathlib import Path

# Racine du dépôt : .../swisspipe/tests/test_core_purity.py -> remonte de 3.
REPO_ROOT = Path(__file__).resolve().parents[2]
CORE_DIR = REPO_ROOT / "swisspipe" / "core"
DOMAIN_DIR = CORE_DIR / "domain"

# Bannis dans tout le cœur (préfixes : "x" couvre "x" et "x.<sub>").
BANNED_ALL: tuple[str, ...] = (
    "sqlalchemy",
    "fastapi",
    "starlette",
    "uvicorn",
    "alembic",
    "psycopg",
    "swisspipe.adapters",
    "swisspipe.persistence",
)
# Banni en plus dans core/domain.
BANNED_DOMAIN: tuple[str, ...] = ("pydantic",)


def _module_dotted_path(py_file: Path) -> str:
    """Chemin module pointé relatif à la racine, ex. swisspipe.core.domain.x."""
    rel = py_file.resolve().relative_to(REPO_ROOT).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _resolve_relative(module: str | None, level: int, current_pkg: str) -> str:
    """Résout un import relatif (level>0) en chemin absolu pointé."""
    base_parts = current_pkg.split(".")
    # level=1 -> paquet courant ; level=2 -> parent ; etc.
    base_parts = base_parts[: len(base_parts) - (level - 1)] if level >= 1 else base_parts
    if module:
        base_parts = base_parts + module.split(".")
    return ".".join(base_parts)


def _imported_modules(tree: ast.AST, current_pkg: str) -> list[tuple[str, int]]:
    """(module absolu pointé, numéro de ligne) pour chaque import du fichier."""
    found: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.append((alias.name, node.lineno))
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                found.append((_resolve_relative(node.module, node.level, current_pkg), node.lineno))
            elif node.module:
                found.append((node.module, node.lineno))
    return found


def _is_banned(module: str, banned: tuple[str, ...]) -> str | None:
    for prefix in banned:
        if module == prefix or module.startswith(prefix + "."):
            return prefix
    return None


def _core_py_files() -> list[Path]:
    return sorted(p for p in CORE_DIR.rglob("*.py"))


def test_core_dir_exists() -> None:
    assert CORE_DIR.is_dir(), f"core introuvable : {CORE_DIR}"


def test_core_imports_are_pure() -> None:
    violations: list[str] = []
    for py_file in _core_py_files():
        pkg = _module_dotted_path(py_file)
        is_domain = DOMAIN_DIR in py_file.resolve().parents
        banned = BANNED_ALL + (BANNED_DOMAIN if is_domain else ())
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for module, lineno in _imported_modules(tree, pkg):
            hit = _is_banned(module, banned)
            if hit:
                rel = py_file.relative_to(REPO_ROOT)
                violations.append(
                    f"{rel}:{lineno}: import interdit '{module}' (préfixe banni '{hit}')"
                )
    assert not violations, "Cœur impur — imports interdits trouvés :\n" + "\n".join(violations)


# Modules transverses (L2) — doivent rester dans le périmètre PUR du cœur.
_TRANSVERSES = (
    "swisspipe.core.domain.modele",
    "swisspipe.core.domain.instance",
    "swisspipe.core.domain.montage",
    "swisspipe.core.domain.role",
    "swisspipe.core.domain.role_affectation",
)


def test_modele_et_instance_couverts_et_purs() -> None:
    """§5 : Modèle + Instance sont scannés par le garde-fou ET n'importent rien d'interdit.

    Le domaine transverse (modele.py, instance.py) doit rester 100% stdlib : aucune lib
    d'infra, aucune couche adapters/persistence, et pas de pydantic (règle domaine).
    """
    scannes = {_module_dotted_path(p) for p in _core_py_files()}
    for mod in _TRANSVERSES:
        assert mod in scannes, f"{mod} hors du scan de pureté du cœur"

    violations: list[str] = []
    for py_file in _core_py_files():
        name = _module_dotted_path(py_file)
        if name not in _TRANSVERSES:
            continue
        banned = BANNED_ALL + BANNED_DOMAIN  # domaine : pydantic banni en plus
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for module, lineno in _imported_modules(tree, name):
            hit = _is_banned(module, banned)
            if hit:
                violations.append(f"{name}:{lineno}: import interdit '{module}' (banni '{hit}')")
    assert not violations, "Modèle/Instance impurs :\n" + "\n".join(violations)


def test_calcul_montage_aware_reste_dans_le_coeur_pur() -> None:
    """§5 étape 4 : le calcul de droits effectifs montage-aware reste agnostique.

    Fichier de services (pydantic toléré) : on interdit juste l'infra + les couches
    adapters/persistence (BANNED_ALL). Aucune évaluation live, aucun NC dans le calcul.
    """
    mod = "swisspipe.core.services.droits_effectifs"
    scannes = {_module_dotted_path(p) for p in _core_py_files()}
    assert mod in scannes, f"{mod} hors du scan de pureté du cœur"
    py = next(p for p in _core_py_files() if _module_dotted_path(p) == mod)
    tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
    violations = [
        f"{mod}:{lineno}: {module}"
        for module, lineno in _imported_modules(tree, mod)
        if _is_banned(module, BANNED_ALL)
    ]
    assert not violations, "Calcul de droits impur :\n" + "\n".join(violations)


def test_delta_projection_reste_dans_le_coeur_pur() -> None:
    """§5 étape 8 : le delta de projection (reconcile) est data-pure, zéro occ/infra."""
    mod = "swisspipe.core.services.delta_projection"
    scannes = {_module_dotted_path(p) for p in _core_py_files()}
    assert mod in scannes, f"{mod} hors du scan de pureté du cœur"
    py = next(p for p in _core_py_files() if _module_dotted_path(p) == mod)
    tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
    violations = [
        f"{mod}:{lineno}: {module}"
        for module, lineno in _imported_modules(tree, mod)
        if _is_banned(module, BANNED_ALL)
    ]
    assert not violations, "Delta de projection impur :\n" + "\n".join(violations)
