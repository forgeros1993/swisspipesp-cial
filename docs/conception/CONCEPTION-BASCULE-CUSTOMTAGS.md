# DOC 3 — Conception : bascule custom_tags prod → cœur SwissPipe

> Conception sur papier. Aucun code, aucune écriture serveur, custom_tags prod `c301e91e` intouchée.
> Migration **à utilisateurs vivants** (back-office Vellis). Progressive, réversible à chaque pas, obsédée par la preuve d'équivalence des accès. Esprit D6 : "fait" = accès effectifs identiques avant/après, prouvés — pas "le script a tourné".

## ⚠️ L'INSIGHT CENTRAL (à trancher en premier) — quelle est la "vérité" à reproduire ?
custom_tags a **deux états contradictoires** :
1. **L'ACL réelle NC appliquée** = le groupe société est lié au Group Folder en **`write delete share` PLAT** pour tous ses membres. C'est ce qui **gouverne réellement** l'accès aujourd'hui. Grossier : un "lecture" et un "full" ont le même write réel.
2. **Le modèle `custom_tags_permissions`** (read/write/full/ghost + additionnels) = l'**intention fine** affichée dans l'éditeur de droits — **JAMAIS appliquée** (modèle fantôme, 0 écriture ACL, prouvé à l'audit).

Conséquence décisive :
- **Seed depuis l'ACL RÉELLE (option A)** → le cœur reproduit exactement l'accès actuel → la bascule **ne change aucun accès** (transfert du mécanisme de pilotage seulement). Shadow → divergence nulle atteignable. **Rayon de souffle = 0.**
- **Seed depuis le modèle fin (option B)** → appliquer enfin l'intention → **change l'accès réel** (le "lecture" perd son write). C'est un **resserrage de sécurité légitime mais à revoir utilisateur par utilisateur** — ce n'est PAS une bascule neutre.

**Reco forte** : **option A pour la bascule** (transfert de mécanisme à accès constant, prouvable à divergence nulle), PUIS, séparément, un **resserrage délibéré** (option B) comme changement à part entière, revu et tracé. Le garde-fou "seed depuis l'ACL réelle, jamais le fantôme" pointe exactement vers A. → **Arbitrage PM** : confirmer A (bascule neutre puis durcissement) vs B (durcir pendant la bascule). Je recommande A sans réserve : on ne mélange pas "changer de moteur" et "changer les droits".

## 3.a — INVENTAIRE de l'état réel (la photo "qui a accès à quoi, aujourd'hui")
Source de vérité = **l'ACL/membership NC réel**, PAS le fantôme. Lecture seule.
- Group Folders existants (= sociétés) : id, nom, chemin, groupes liés + **permissions réelles** (`group_folders_acl` + `group_folders_groups` + le flag `write delete share` posé à la création).
- Membres de chaque groupe (`oc_group_user`).
- Hiérarchie custom_tags (`custom_tags_hierarchy` : société/département/projet, systemtag_id) + le mapping nœud↔chemin physique (dérivé via `parent_id`).
- (Pour info/réconciliation future, pas pour le seed) : `custom_tags_permissions` (le fantôme) et les co-accès — utiles pour COMPRENDRE l'intention, pas pour fixer l'accès.
- **Livrable** : un export "photo" horodaté = pour chaque (groupe, ressource) les permissions réelles. C'est la référence avant/après.

## 3.b — SEED du cœur (traduire la photo dans le modèle possédé)
Réutilise `docs/analyse-prototype-custom-tags.md` (ne pas refaire la carte). En **option A** :
- **Dimensions** : Société (et Département si on confirme 2 dimensions, cf. D3 "2 dimensions couvrent 100% des besoins réels"). Projet = ressource/sous-chemin, pas dimension (à confirmer).
- **Espaces** = combinaisons de coordonnées dimensionnelles (les Group Folders sociétés → `Espace` + `ressource_mapping` interne↔folder_id/chemin).
- **Groupes** : les groupes société NC → `Groupe` organisationnel. **Les droits `'user',uid` directs du fantôme → si on les enforce un jour (option B), chacun devient un `Groupe` personnel (1 humain)** — INV-4. En option A on ne seed PAS les octrois 'user' (ils ne sont pas appliqués en réalité) ; on seed l'octroi **organisationnel** qui reflète le `write delete share` réel.
- **Octrois** : pour chaque groupe société → une `Matrice` = la traduction du `write delete share` réel (≈ ÉCRITURE+SUPPRESSION selon les bits réellement posés). Mode `MODIFIER` à la racine de l'espace.
- **ressource_mapping** : peuplé ici (UUID interne ↔ folder_id/chemin NC).
- **Cas ambigus à lister** (arbitrage) : (1) `ghost`/REFUSER présents dans le fantôme mais non appliqués → en A, on ne les enforce pas (note pour B) ; (2) un `'user',uid` direct → en A ignoré (non appliqué), en B → groupe personnel ; (3) départements/projets sans ACL propre (héritent du folder société) → mode HÉRITER.

## 3.c — PHASE SHADOW (le garde-fou central, type "computed avant/après = vide" de D6)
Le cœur tourne en **DRY-RUN** : il lit l'ACL réelle NC, calcule l'état désiré depuis son modèle seedé (option A), et **compare** (`comparer_droits`) **sans rien écrire**.
- **Critère de passage** : `Divergence.est_conforme == True` pour l'espace (ou tous les écarts listés, compris, et voulus). En option A, la cible = **divergence nulle** (le seed vient de l'ACL réelle, donc le désiré doit == le réel).
- Si divergence non nulle en A → **bug de seed/traduction**, pas un changement voulu → on corrige le seed avant d'avancer. (En B, la divergence est attendue = le durcissement ; on la revoit ligne par ligne.)
- Le cron en mode shadow (DOC 2.b) **fait tourner ça en continu** et journalise.
- **Tant que le shadow d'un espace n'est pas à divergence nulle, on ne bascule pas cet espace.**

## 3.d — COEXISTENCE : qui pilote quoi pendant la transition
Danger : custom_tags **et** le cœur peuvent tous deux écrire des ACL sur le **même** NC → conflit (l'un défait l'autre).
- **Option (i) — geler custom_tags globalement** (lecture seule sur les droits) AVANT que le cœur prenne la main : simple, pas de conflit, mais big-bang (toutes les sociétés d'un coup, rayon de souffle global).
- **Option (ii) — bascule espace par espace** : un drapeau "cet espace est piloté par le cœur" ; custom_tags continue les autres, le cœur prend les espaces basculés ; chaque société migre isolément. Plus sûr (rayon de souffle = 1 société), mais nécessite que custom_tags **respecte** le drapeau (ne plus toucher les ACL d'un espace basculé) — or custom_tags ne connaît pas le cœur. → mitigation : geler custom_tags **par société** (retirer sa capacité d'écrire l'ACL de cette société : neutraliser le bouton/endpoint pour cet espace), ou accepter qu'en pratique un admin ne touche pas deux fois la même société.
- **Reco** : **(ii) bascule par espace, avec gel par-société de custom_tags** sur l'espace migré. Le rayon de souffle limité prime. Le "gel par société" = la garantie anti-conflit. **Arbitrage PM** : (i) gel global plus simple mais risqué vs (ii) par-espace plus sûr mais demande le gel ciblé de custom_tags.

## 3.e — BASCULE PROGRESSIVE (par espace)
1. Choisir l'**espace pilote** = le **moins risqué** : peu d'utilisateurs, pas de co-accès actif, idéalement une société "interne"/test plutôt qu'un gros client. **Arbitrage PM** : laquelle ? (besoin de la liste réelle des sociétés + leur criticité — je ne la devine pas).
2. Shadow de cet espace à **divergence nulle** (3.c) — prouvé.
3. Geler custom_tags sur cet espace (3.d option ii).
4. Passer le cron/CLI de **dry-run → apply** pour CET espace uniquement (`appliquer_droits` pose l'état désiré = identique au réel en option A → **aucun changement effectif**, juste le cœur devient la source).
5. **Vérification post-bascule** (3.f) : les utilisateurs de l'espace ont **exactement** leurs accès (ni plus ni moins).
6. **Rollback** : si problème → re-dégeler custom_tags sur cet espace + remettre le cron en dry-run ; l'ACL réelle n'ayant pas changé (option A), le retour est neutre. (Backup ACL avant apply de toute façon.)
7. Généraliser société par société, dans l'ordre croissant de risque.

## 3.f — CRITÈRES DE PREUVE par phase ("fait" ≠ "le script a tourné")
- **Inventaire** : la photo couvre 100% des Group Folders/groupes/membres (compté), pas un échantillon.
- **Seed** : chaque ressource a un `ressource_mapping` ; `reconcilier_tout --dry-run` ne lève aucune `RessourceNonMappeeError`.
- **Shadow** : `Divergence.est_conforme` pour l'espace (option A = divergence nulle), prouvé par sortie de `comparer_droits`.
- **Post-bascule** : pour chaque utilisateur de l'espace, `lire_droits_effectifs` après == la photo d'inventaire avant (diff vide). C'est le "computed avant/après = vide" appliqué aux droits.
- **Journal** : chaque apply a une entrée append-only avec cause `bascule-<espace>`.

## 3.g — RISQUES SPÉCIFIQUES + mitigation
| Risque | Mitigation |
|---|---|
| Utilisateurs connectés pendant la bascule | Option A = aucun changement d'accès → transparent ; faire l'apply hors heures de pointe par prudence ; pas de déconnexion forcée |
| Accès retiré par erreur (rayon de souffle) | Bascule **par société** (rayon = 1) ; backup ACL avant apply ; rollback neutre (option A) ; preuve diff-vide AVANT de déclarer fait |
| Le fantôme custom_tags diverge de l'ACL réelle | **Seed depuis l'ACL réelle, jamais le fantôme** (garde-fou) → le décalage fantôme↔réel est ignoré pour la bascule (et documenté comme la dette que B corrigera) |
| custom_tags et cœur écrivent en même temps | Gel par-société de custom_tags sur l'espace basculé (3.d) |
| VDI Vellis (1 user/session) | Sans impact sur les ACL Group Folder (par groupe, pas par session) ; à confirmer que la bascule ne touche pas les sessions VDI — a priori non |
| Postgres dédié pas encore en prod | Prérequis infra : provisionner la base possédée AVANT le seed (hors cœur, décision infra) |

## Arbitrages PM (DOC 3)
1. **Option A (bascule neutre puis durcissement) vs B (durcir pendant)** — je recommande **A** fortement.
2. **Coexistence (i) gel global vs (ii) par-espace** — je recommande **(ii)**.
3. **Société pilote + ordre de bascule** — besoin de la liste réelle + criticité (ne pas deviner).
4. **Dimensions** : confirmer 2 dimensions (Société, Département) + Projet=ressource (cf. D3).
5. **Postgres dédié** : qui/quand le provisionne (prérequis).
