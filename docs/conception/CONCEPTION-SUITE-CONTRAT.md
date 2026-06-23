# DOC 1 — Conception : suite de contrat paramétrée (la preuve d'agnosticité)

> Conception sur papier. Aucun code écrit. À valider par le PM avant implémentation.
> Priorité : cette suite VERROUILLE l'agnosticité. Tant qu'elle n'est pas verte sur fake **ET** NC, "Nextcloud est remplaçable" reste une promesse, pas une preuve.

## Problème
Aujourd'hui l'interchangeabilité du port `AdaptateurRessource` = **2 `isinstance`** (`test_adaptateur_memoire.py:85`, `test_traduction_nextcloud.py:155`). Ça prouve la **forme** (5 méthodes présentes via `runtime_checkable`), pas le **comportement**. Un adaptateur peut satisfaire le Protocole et faire n'importe quoi.

## 1.a — La suite paramétrée (cas de comportement, indépendants de l'implémentation)
Principe : une fixture `adaptateur` paramétrée fournit successivement le fake et le NC ; **les mêmes fonctions de test** s'exécutent contre chacun. Le test ne connaît jamais l'implémentation — il pose un état via le contrat et le relit via le contrat.

| # | Cas | Comportement attendu | Vérification (boîte noire) |
|---|---|---|---|
| C1 | **creer_ressource** | retourne une clé externe non vide ; la ressource existe ensuite | `cle = creer(descr)` puis `lire_droits_effectifs(cle)` ne lève pas |
| C2 | **archiver = réversible, jamais delete dur** (INV-5) | après archivage, la ressource n'est plus "active" mais ses données subsistent | archiver(cle) → l'adaptateur ne supprime pas la donnée (vérif via capability `peut_relire_apres_archive` ou état ARCHIVÉ exposé) |
| C3 | **renommer** | le nom externe change, la clé externe et les droits restent | renommer(cle,nom) → `lire_droits_effectifs(cle)` inchangé |
| C4 | **appliquer_droits idempotent** | appliquer 2× le **même** état désiré ⇒ état final identique, aucune erreur | `appliquer(cle,E)`; `r1=lire`; `appliquer(cle,E)`; `r2=lire`; `assert r1==r2==E` |
| C5 | **appliquer_droits = état COMPLET (pas un diff)** | un groupe présent puis **retiré** de l'état désiré est bien retiré côté exécutant | `appliquer(cle,{g1,g2})`; `appliquer(cle,{g1})`; `assert lire == {g1}` (g2 disparu) |
| C6 | **lire_droits_effectifs reflète l'appliqué** | ce qu'on lit == ce qu'on a posé (round-trip) | `appliquer(cle,E)`; `assert lire(cle) == E` (modulo capability, cf. 1.c) |
| C7 | **réconciliation : dérive détectée → corrigée** | une modif "externe" (hors cœur) crée une divergence ; `comparer_droits` la voit ; ré-appliquer rétablit | poser E ; muter l'état réel hors contrat ; `comparer(E, lire)` ≠ conforme ; `appliquer(E)` ; `comparer` conforme |
| C8 | **REFUSER/deny réalisé** | un `DroitGroupe` correspondant à un REFUSER résolu ⇒ l'exécutant retire/bloque l'accès du groupe (pas juste "absent du modèle") | poser un état avec un groupe à matrice "bloquée" ⇒ `lire` ne lui accorde aucun droit positif |
| C9 | **INV-4 : groupe obligatoire** | un `DroitGroupe` à `groupe_id` vide ⇒ refus | `appliquer(cle,{DroitGroupe("",…)})` lève `ValueError` |

Note C7/C8 : le cœur (`reconciliation.py`, `droits_effectifs.py`) calcule l'état désiré ; la suite de contrat teste l'**adaptateur**, donc elle pose directement des `frozenset[DroitGroupe]` (sortie cœur déjà résolue) — pas de logique métier dans le test.

## 1.b — Branchement fake / NC sur la même suite
- **fake** (`AdaptateurMemoire`) : hermétique, en mémoire, toujours exécuté (CI sans dépendance).
- **NC** (`AdaptateurNextcloud`) : contre un **Nextcloud de test** (idéalement un conteneur jetable NC+GroupFolders, ou à défaut le serveur réel mais **exclusivement** sur des Group Folders `zztest_` id>20 + groupes `zztest_grp_*`, cleanup `finally` — jamais les dossiers prod 4-20). `skipif` propre si pas d'accès SSH/serveur (comme aujourd'hui).
- **Ce qui prouve l'agnosticité** : la fixture paramétrée fait passer **les mêmes C1–C9** aux deux. Si les deux sont verts, l'interchangeabilité est démontrée en comportement, pas en forme.

## 1.c — Divergences légitimes (capability flags)
Certains cas ne sont pas exigibles de tout exécutant. On les modélise par des **capability flags** déclarés par l'adaptateur, pas par des `if isinstance` dans les tests :
- **TÉLÉCHARGEMENT = 0 bit côté NC** (non mappable en permissions Group Folder, cf. `traduction.py` + question ouverte Q-téléchargement) : le fake peut le porter, le NC non. → flag `supporte_telechargement_distinct`. C6 (round-trip) tolère la **perte documentée** : on compare le **comparable** (le NC ne reconstruit pas CLASSEMENT/TÉLÉCHARGEMENT depuis les bits — déjà acté dans `permissions_nextcloud_vers_matrice`).
- **Deny explicite sous-chemin** (REFUSER sur une sous-ressource) : aujourd'hui L2 côté NC (deny racine seulement). → flag `supporte_deny_sous_chemin` (faux pour NC en L1). C8 ne teste que le deny racine pour les adaptateurs sans ce flag.
- Règle : un flag à `False` **saute le sous-cas**, il ne le fait pas passer en trichant. Chaque flag est une **dette explicite tracée**, pas un trou caché.

## Ce que ça transforme
Une fois C1–C9 verts sur fake **et** NC : l'agnosticité passe de "conçue" à **démontrée**. Écrire l'adaptateur Mail (L5) ou remplacer NC = "le faire passer la suite". C'est le **harnais de non-régression du port** : tout futur adaptateur a une définition de "fait" objective.

## Arbitrage PM
- **Nextcloud de test** : conteneur NC jetable (idéal, hermétique CI) vs serveur réel en `zztest_` (plus simple, mais dépend du live + lent). Reco : viser le conteneur jetable à terme ; démarrer en `zztest_` sur le réel pour ne pas bloquer.
- Niveau d'exigence des capability flags : combien de divergences "légitimes" on tolère avant de considérer un adaptateur "non conforme" ?
