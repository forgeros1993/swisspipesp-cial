# AUDIT DE COMPLÉTUDE & QUALITÉ — cœur swisspipesp-cial

**Audité** : `~/swisspipesp-cial`, commit `12bb783` (2026-06-16), branche `main`, arbre propre. Cœur hexagonal SwissPipe + adaptateur Nextcloud. Claim revendiqué : "L1 BOUCLÉ" (CLAUDE.md §8 dit "176 tests verts").
**Méthode** : lecture du code + **exécution réelle de la suite** (venv jetable + Postgres jetable docker) + grep. Chaque verdict = preuve.
**Garde-fous** : custom_tags prod `c301e91e` **INTOUCHÉE**. Aucun test écrivant sur le vrai serveur NC n'a été lancé (grandeur_nature + detection_upgrade exclus). PG de test = conteneur docker jetable, supprimé après. Date : 2026-06-23.

---

## 0 — RÉSULTAT RÉEL DES TESTS (vs claim)

| | Réel (exécuté par moi) |
|---|---|
| `pytest` hermétique (sans PG/SSH) | **191 passed, 24 skipped** (52 s) |
| Les 24 skips | **TOUS** `DATABASE_URL_TEST non défini — Postgres requis` (pas SSH) : `test_persistence` ×7, `test_reconciliation_service` ×12, `test_detection_upgrade` ×4, `test_reconciliation_grandeur_nature` ×1 |
| Re-run avec **Postgres jetable** (docker) sur `test_persistence` + `test_reconciliation_service` | **19 passed** ✅ (j'ai exécuté les ex-skips de persistance + orchestrateur) |
| **Total vérifié par moi** | **210/215** (191 hermétiques + 19 PG) |
| Non lancés par moi (real-NC SSH en écriture) | **5** : `detection_upgrade` ×4 + `grandeur_nature` ×1 — exclus par prudence (écrivent sur le vrai serveur, même en `zztest_`) |

**Claim "191 verts" = EXACT aujourd'hui.** "176" de CLAUDE.md = périmé-bas. **Nuance importante** : les 191 hermétiques = la moitié domaine-pur + fake + traduction ; **toute la persistance réelle (trigger journal) + l'orchestrateur de réconciliation skippent sans Postgres**. Je les ai donc lancés moi-même avec un PG → **19/24 passent**. Restent 5 dépendants du vrai serveur NC (round-trip end-to-end), que je n'ai PAS exécutés → leur preuve reste **sur-parole** (le code existe, conçu sûr, mais non re-exécuté ici).

**Tests "vrai serveur" (SSH occ + dossiers `zztest_`)** : `test_reconciliation_grandeur_nature.py` (écrit un Group Folder `zztest_`, id>20, prod 4-20 jamais touchés, cleanup `finally`), `test_detection_upgrade.py`, `test_adaptateur_nextcloud_integration.py`. Proportion dépendante du live = ~5 tests. Le reste est hermétique ou PG-local.

---

## 1 — PURETÉ DU CŒUR (agnosticité) — **PROUVÉ**

- `test_core_purity.py` (2 tests) **exécuté = PASS**. AST-walk de `core/**`, échoue si import de `sqlalchemy/fastapi/starlette/uvicorn/alembic/psycopg/swisspipe.adapters/swisspipe.persistence` (+`pydantic` banni dans `core/domain`). Indépendant de ruff.
- Contre-vérif manuelle : `grep -riE 'nextcloud|occ|ssh|groupfolder|webdav|OCP|psycopg|sqlalchemy|subprocess' core/` = **13 hits, TOUS en commentaires/docstrings/ruff.toml** (ex. `ressource.py:17` "NE porte JAMAIS son identifiant externe (chemin WebDAV…)") — **0 import, 0 appel**.
- **Verdict : pur prouvé** (test exécuté + 0 import infra). C'est l'opposé exact de custom_tags (198 `OCP\` dans la logique).

---

## 2 — VERDICT DÉCISIF : l'adaptateur écrit-il VRAIMENT les ACL ?

**OUI — prouvé par le code qui s'exécute (pas un fantôme comme custom_tags).**

- **2.a Exécution réelle** : `appliquer_droits` (`adaptateur_nextcloud.py:118-146`) → `self._occ([...])` → `executer_occ` → **`subprocess.run(["ssh","-o","BatchMode=yes",alias, "…php occ groupfolders:permissions …"])`** (`occ_runner.py:48-51`). Active l'ACL (`-e`), pose les 5 verbes par groupe (`_poser_regle` : `clear` puis verbes), **clear les groupes retirés** (réconciliation). État complet idempotent, pas un diff. **C'est une vraie commande exécutée**, pas un stub. (Contraste custom_tags : 0 écriture ACL.)
- **2.b Traduction matrice→bitmask** (`traduction.py`, conforme NC read=1/update=2/create=4/delete=8/share=16) : `LECTURE→1`, `ÉCRITURE→3`, `SUPPRESSION→11`, `CRÉATION→+4`, `CLASSEMENT→+create|delete`. `share(16)` jamais accordé. **`TÉLÉCHARGEMENT→AUCUN bit`** = question ouverte assumée (non mappable en bits NC, à traiter via `files_accesscontrol`). Honnête, documenté.
- **2.c lire_droits_effectifs** : lit le **vrai** NC (`executer_select` SELECT-only sur `group_folders_acl JOIN filecache`). Cœur `reconciliation.py` : `etat_desire()` + `comparer_droits()` → `Divergence(groupes_manquants/en_trop/matrices_divergentes, est_conforme)`. Round-trip apply→read symétrique.
- **Idempotence + détection de dérive** : testées dans `test_reconciliation_service` (**12 tests exécutés par moi avec PG = PASS** : no-op si conforme, trace journal par groupe, balayage résilient) + `grandeur_nature` (round-trip contre le vrai NC, **NON relancé par moi** = sur-parole).
- **Verdict 2 : l'adaptateur applique RÉELLEMENT** (exécution prouvée par lecture du code subprocess + tests d'orchestration PG verts). **Seule réserve** : le bout end-to-end contre le vrai NC (`grandeur_nature`) n'a pas été ré-exécuté ici → cette dernière marche reste sur-parole.

---

## 3 — COUVERTURE INVARIANTS & MODÈLE

| Critère | Constat | Preuve | Verdict |
|---|---|---|---|
| **INV-4** (droit porté par groupe, jamais personne) | Modèle `Octroi` = mode+matrice, **aucun champ user/personne** ; cible = `DroitGroupe.groupe_id` ; adaptateur `-g` only + `raise` si `groupe_id` vide (`:137`) | `octroi.py`, `adaptateur_ressource.py:46`, `adaptateur_nextcloud.py:137` ; `test_ressource` échoue exprès si on ajoute un id externe | **prouvé** (structurellement impossible d'octroyer à une personne ; contraste custom_tags `'user',uid`) |
| **Modes Hériter/Modifier/Refuser** | Cascade enfant→racine : REFUSER court-circuite (§9.3), MODIFIER surcharge, HERITER remonte, deny-by-default ; multi-groupe = max positif (REFUSER n'écrase pas un autre groupe) | `droits_effectifs.py:88-89` ; `test_droits_effectifs` (19, hermétiques, PASS) | **prouvé** (limite REFUSER sous-chemin = L2 clairement déclarée) |
| **Matrice** L⊂É⊂S + additionnels + fusion | `NiveauPrincipal` inclusif (`inclut`), `DroitAdditionnel` indépendant, `Matrice.fusionner` additif commutatif/idempotent | `matrice.py` ; `test_matrice` (28, PASS) | **prouvé** |
| **Renversement** | Projection "voir par X→Y" sur frozenset d'espaces plats + ordre de dimensions → `ArbreNavigation` ; pivot partiel ; ne filtre pas les droits (amont) | `renversement.py` ; `test_renversement` (11, PASS) | **prouvé** |
| **Journal append-only (INV-6)** | Trigger DB `journal_acces_append_only()` `RAISE EXCEPTION` sur UPDATE/DELETE/TRUNCATE | migration `0001` ; **`test_journal_append_only` + `test_journal_truncate_rejete` exécutés par moi avec PG = PASS** (`raises(DBAPIError, match="append-only")`) | **prouvé-exécuté** |
| **Données possédées** | Schéma Postgres possédé via Alembic ; les **19 tests PG que j'ai lancés construisent et utilisent le schéma complet** (dimension/valeur/espace/coordonnee/groupe/membre/ressource/mapping/octroi/journal/etat_systeme) — sinon ils échoueraient | migrations `0001/0002` ; mon run PG = 19 PASS | **prouvé-exécuté** |

---

## 4 — DETTE HONNÊTE (le "bouclé" est-il tenu ?)

- **4.a Inbound = STUB confirmé** : `adapters/inbound/` = `.gitkeep` + `__init__.py` vide. **Aucun FastAPI/CLI/cron.** Les services appelables existent (`application/reconciliation_service.py` : `verifier_et_reconcilier`, `reconcilier_tout`) mais **rien ne les expose**. → **LE chaînon manquant pour rendre le cœur opérant.**
- **4.b TODO/stub** : **1 seul** dans tout le code non-test — un **docstring périmé** (`adaptateur_nextcloud.py:14` "Tranche C → NotImplementedError") alors que le code l'implémente pleinement. **Cosmétique.** Pas de `pass`/stub structurant.
- **4.c Suite de contrat = FAIBLE** : l'interchangeabilité fake↔NC repose sur **2 `isinstance(a, AdaptateurRessource)`** (`test_adaptateur_memoire.py:85`, `test_traduction_nextcloud.py:155`) — pas une suite paramétrée partagée où les deux adaptateurs passent les MÊMES scénarios comportementaux. → **dette de preuve d'agnosticité** : le port est prouvé "même forme" (5 méthodes, runtime_checkable), pas "même comportement".
- **4.d Frontière L1/L2** : déféré L2 explicitement = REFUSER override **sous-chemin** (deny racine ✅ / sous-chemin ❌-L2), **plafond de montage**, **rôles**, **gel-as-service** (enum GEL/DEGEL existe, service non), **espaces transverses/montages** (ADR-0013 "proposé"). Ouverte : **Q-téléchargement** (0 bit). → **Frontière cohérente** : ce qui est "L1" (1 ordre dimensionnel, matrice, modes racine, réconciliation, journal) est livré ; les morceaux L2 sont annoncés, pas faussement comptés en L1.

---

## CLAIMS VÉRIFIÉS vs OPTIMISTES

**Vérifiés (prouvés-exécutés par moi) :**
- 191 hermétiques verts + 19 PG verts (persistance, **trigger journal append-only**, orchestrateur réconciliation).
- Cœur pur (test AST + 0 import infra).
- Adaptateur **écrit réellement** l'ACL (subprocess ssh occ, lu en clair) — **pas un 2e fantôme**.
- INV-4 structurel, modes, matrice, renversement, journal, schéma possédé.

**Sur-parole / optimistes (non re-vérifiés ici) :**
- Le round-trip **end-to-end contre le vrai Nextcloud** (`grandeur_nature`, `detection_upgrade`) — non relancé (écrit sur prod NC). Le code est là et conçu sûr, mais "prouvé contre le vrai NC" repose sur le claim, pas sur mon exécution.
- "Port interchangeable" = prouvé en forme, **pas en comportement** (2 isinstance, pas de suite de contrat partagée).
- CLAUDE.md "176 verts" = chiffre périmé (réel 191).

---

## CE QUI RESTE POUR RENDRE L1 OPÉRANT (ordonné)
1. **Inbound** (FastAPI/CLI/cron) branché sur `reconciliation_service` — le seul vrai bloqueur d'opérationnalisation.
2. **Suite de contrat paramétrée** fake↔NC (mêmes scénarios) — solder la dette de preuve d'agnosticité.
3. **Q-téléchargement** (mapping via `files_accesscontrol`) — question ouverte.
4. (Re-jouer `grandeur_nature` contre un NC de test pour lever le "sur-parole" — quand un env de test NC dédié existe.)

---

## VERDICT (sans complaisance)

**Le cœur tient ses promesses — réutilisable comme fondation, AVEC réserves opérationnelles, PAS de conception.** Les claims décisifs sont prouvés par exécution : cœur agnostique réel (0 import NC, test AST vert), adaptateur qui **écrit vraiment** les ACL groupfolders (subprocess ssh occ, l'inverse de custom_tags), journal append-only **imposé par trigger DB et vérifié**, INV-4/modes/matrice/renversement testés. La distance au "100% bouclé" est faite de **trous d'OPÉRATIONNALISATION et de PREUVE** (inbound stub, suite de contrat faible, end-to-end vrai-NC sur-parole, Q-téléchargement) — **rattrapables**, pas de défaut de fondation. C'est une base saine pour remplacer custom_tags ; on peut bâtir dessus à condition de solder (1) l'inbound et (2) la suite de contrat avant de revendiquer l'agnosticité comme acquise en production.
