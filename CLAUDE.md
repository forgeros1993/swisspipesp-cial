# SwissPipe — Cœur de gouvernance des accès

> Fichier de reprise de contexte. À lire en début de chaque session.

## 1. Principe directeur — architecture hexagonale (ports & adapters)

Le **cœur de décision d'accès ne connaît AUCUN système externe** (ni Nextcloud, ni
ERP, ni mail, ni bâtiment). Il décide « qui peut quoi sur quelle ressource » de façon
**agnostique** et **auditable par lecture**.

- `swisspipe/core/` — cœur pur. **Interdit** d'y importer une lib externe métier
  (pas de client Nextcloud, pas de SDK, pas de SQLAlchemy dans le domaine). Le cœur
  exprime ses besoins via des **ports** (Protocols).
- `swisspipe/adapters/` — traduit les décisions du cœur vers des exécutants concrets.
  - `inbound/` — entrées (FastAPI / HTTP).
  - `outbound/` — sorties. `fake/` = adaptateur ressource en mémoire (tests).
    `nextcloud/` viendra plus tard.
- `swisspipe/persistence/` — modèles SQLAlchemy + migrations Alembic. **Hors du cœur.**

Règle de dépendance : `adapters → core`, `persistence → core`. Jamais `core → *`.

## 2. Stack imposée

Python 3.12 · FastAPI · SQLAlchemy 2.0 · Alembic · Pydantic v2 · psycopg 3 ·
PostgreSQL · pytest. Qualité : ruff (lint+format) · mypy (strict).

## 3. Invariants NON NÉGOCIABLES

- **INV-1** — un attribut décide **OÙ** un conteneur se monte et quel **PLAFOND** de
  droits, jamais **QUI**. Désigner un bénéficiaire = acte humain.
- **INV-2** — quand une donnée source change la topologie, les droits existants sont
  **GELÉS** (sortis de l'état courant + tracés au journal), jamais détruits, jamais
  réactivés automatiquement.
- **INV-3** — **pas d'ABAC dynamique**. Les droits sont posés à un instant déclaré et
  figés, jamais recalculés en temps réel.
- **INV-4** — aucun droit attaché à une personne en direct. Tout passe par un
  **groupe** (organisationnel ou personnel-à-1-humain).
- **INV-5** — l'API crée/édite/archive et remplit des rôles, **jamais ne supprime**,
  jamais ne nomme une personne.
- **INV-6** — l'audit se **LIT** (état courant + journal append-only), il ne se
  **CALCULE** pas.

## 4. Glossaire figé (terminologie verrouillée)

| Terme | Définition |
|---|---|
| **Dimension** | Axe de classification (ex. site, projet, service). |
| **Valeur de dimension** | Valeur concrète prise sur une dimension. |
| **Espace (dimensionnel)** | Combinaison de valeurs de dimensions définissant un emplacement logique. |
| **Espace transverse** | Espace qui traverse plusieurs valeurs/dimensions (cf. ADR-0013). |
| **Modèle** | Gabarit réutilisable d'une structure (topologie, plafonds). |
| **Instance** | Matérialisation concrète d'un modèle. |
| **Montage / point de montage** | Emplacement où un conteneur est rattaché dans la topologie. |
| **Portée de montage** | Étendue couverte par un montage. |
| **Rôle** | Fonction tenue par un groupe sur une ressource (rempli, jamais nommé à une personne). |
| **Groupe personnel** | Groupe à 1 humain (indirection obligatoire, cf. INV-4). |
| **Groupe organisationnel** | Groupe collectif (équipe, service). |
| **Ressource** | Objet gouverné (conteneur, dossier, exécutant abstrait). |
| **Adaptateur** | Traducteur d'une décision du cœur vers un système concret. |
| **Matrice de droits** | Lecture ⊂ Écriture ⊂ Suppression + additionnels : Création, Classement, Téléchargement. |
| **Modes** | Hériter / Modifier / Refuser. |
| **Journal d'accès** | Trace append-only des décisions et gels (cf. ADR-0014, INV-6). |

## 5. Conventions de code

- Nommage : `snake_case` (fonctions, variables, colonnes), `PascalCase` (classes).
- Identifiants : **UUID** (pas d'entiers auto-incrémentés exposés).
- Timestamps : UTC, colonnes `created_at` / `updated_at` (aware).
- **Journal = append-only** : aucune update/delete sur le journal d'accès (INV-2, INV-6).
- Ports = `typing.Protocol`. Le cœur dépend des Protocols, pas des implémentations.
- Cœur pur : zéro import de lib externe métier dans `swisspipe/core/`.
- Typage : mypy strict. Lint/format : ruff.

## 6. Découpage en lots (L1 → L5) — référence projet

Découpage par CAPACITÉ PRODUIT LIVRABLE (pas par couche technique).
Chaque lot livre quelque chose de démontrable.

- L1 — Cœur + adaptateur Nextcloud : dimensions, valeurs de dimension, espaces
  dimensionnels, coordonnées, matrice de droits, modes (Hériter/Modifier/Refuser),
  renversement (priorité produit n°1), groupes (perso/orga), octrois (état courant),
  ressources + ressource_mapping, journal append-only. Adaptateur Nextcloud réel
  (files_accesscontrol, WebDAV, Group Folders). Fondation complète et démontrable.
- L2 — Transverses & montages : modèles, instances, montages à deux clés (consentement
  hôte), portée de montage, pattern self-service (montage dans espace personnel).
- L3 — Métadonnées & sas : schéma de métadonnées par modèle, 3 permissions de méta,
  workflow de validation (gate) sur tout delta qui élargit un accès.
- L4 — Intégration ERP (Odoo) : webhook idempotent (réconciliation sur odoo_project_id),
  re-routing (changement de société = démontage doux + gel), estampillage d'origine.
  La COUTURE (ressource_mapping, cle_reconciliation, systeme_reference, point d'entrée
  "changement → sas") est conçue dès L1-L3 ; le connecteur est branché ici.
- L5 — Adaptateurs additionnels : mail (IMAP/SMTP), bâtiment (contrôleur de portes).
  Valide l'agnosticité a posteriori.

### Séquence de travail INTERNE au L1 (étapes, pas des lots)
1. Domaine : value objects (Matrice, Mode, Dimension, ValeurDimension) + entités
   (Espace, Groupe, Ressource), frozen dataclasses stdlib, invariants dans les types.
2. Services du cœur : renversement (projection navigation, lecture pure) + calcul des
   droits effectifs (héritage + modes, à instant figé INV-3).
3. Ports : contrat AdaptateurRessource (créer/archiver/renommer/appliquerDroits/
   lireDroitsEffectifs) + adaptateur fake en mémoire pour tests.
4. Persistance : modèles SQLAlchemy + migration Alembic, état courant + journal
   append-only (garde-fou anti UPDATE/DELETE).
5. Adaptateur Nextcloud réel (arrive en dernier dans L1, prouve l'agnosticité).

## 7. Notes d'environnement

> Pièges connus — éviter de re-diagnostiquer à chaque session.

- **Git push** : le push **HTTPS échoue** (pas de credentials configurés :
  `could not read Username for 'https://github.com'`). Le remote `origin` est en
  **SSH** : `git@github.com:forgeros1993/swisspipesp-cial.git`. Auth SSH OK comme
  **forgeros1993**. → pousser en SSH, ne pas repasser en HTTPS.
- **Garde-fou cœur pur** : deux niveaux. (1) ruff TID251 via configs imbriquées
  `swisspipe/core/ruff.toml` (+ `core/domain/ruff.toml` qui bannit aussi pydantic) ;
  (2) test ceinture `swisspipe/tests/test_core_purity.py` (AST, stdlib, tourne même
  ruff désactivé). pydantic : interdit `core/domain`, autorisé `core/services`.
- **Tooling local** : ruff absent du système (PEP 668 bloque pip global) → installé
  dans `.venv` (git-ignoré). pytest système 7.4.4 dispo (plugin asyncio absent →
  warning `Unknown config option: asyncio_mode`, sans gravité). `gh` absent.

## 8. État courant

Fondation consolidée : structure, config, ADR proposés, Alembic config, garde-fou
cœur pur (ruff + test). Pas encore de domaine, de service, ni d'endpoint fonctionnel.
Prochain : schéma de données (lot L1/L3).
