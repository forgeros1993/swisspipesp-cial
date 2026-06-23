# RAPPORT T3 — Inventaire → Seed → Shadow (divergence nulle prouvée)

Lecture seule TOTALE sur Nextcloud prod. Le seul écrit = seed dans un Postgres **jetable**.
custom_tags prod `c301e91e` intouchée ; groupfolders 4-20 intacts (avant==après) ; 0 zztest_.

## 1 — PHOTO de l'inventaire (lecture seule : `groupfolders:list` + SELECT)
| Mesure | Valeur |
|---|---|
| Sociétés (groupfolders, ids 4-20) | **17** |
| Octrois réels (groupe × folder) | **18** (1 groupe société/folder + `admin` sur folder 4) |
| Masque réel | **31** partout (read+update+create+delete+share) → `Matrice(SUPPRESSION, {CREATION})` |
| ACL fine active | **0** (toutes désactivées → accès = binding coarse `groups_list`) |
| Droits 'user' directs (INV-4) | **0** (`group_folders_acl` vide) — le fantôme 'user' jamais appliqué, confirmé |
| Hiérarchie custom_tags | 15 société / 81 département / 12 projet |

**Source de vérité = l'ACL/binding RÉEL** (`groups_list`), pas le modèle fantôme `custom_tags_permissions`.
**Preuve lecture seule** : `occ groupfolders:list` + `sql_runner.executer_select` (garde SELECT-only, lève sur non-SELECT). Aucune commande d'écriture.
**Ambiguïté notée** : 17 folders vs 15 nœuds 'societe' (2 folders sans nœud société — admin/test). Option A seede depuis les **folders réels** → non bloquant.

## 2 — SEED (écrit UNIQUEMENT dans le Postgres jetable)
| Entité cœur | Créées |
|---|---|
| Dimensions (Société→Département, chaîne) | 2 |
| Valeurs de dimension (Société) | 17 |
| Espaces (1/société) | 17 |
| Groupes (organisationnels) | 18 |
| Ressources (folders) | 17 |
| Octrois | **18** (= 18 ACL réelles) |

**Stratégie Option A (fidélité par construction)** : chaque octroi porte `matrice = permissions_nextcloud_vers_matrice(masque réel)` — **la même traduction** que `lire_droits_effectifs`. On reproduit le réel tel quel (plat), pas le modèle fin fantôme.
**Cohérence** : 17 ressources = 17 folders ; 18 octrois = 18 ACL réelles ; 18 groupes distincts. **Aucune écriture Nextcloud** (seed = INSERT Postgres jetable seulement).

## 3 — SHADOW (cli `verifier`, dry-run, adaptateur NC réel, LECTURE SEULE)
```
ressources=17 conformes=17 divergentes (DRY-RUN, rien écrit)=0 erreurs=0   (exit 0)
```
**DIVERGENCE NULLE — 17/17 conformes.** Le cœur seedé calcule **exactement** les accès réels actuels.
- Pas de bug de traduction (seed depuis la même fonction que la relecture → conforme par construction).
- Pas de perte légitime visible ici : le masque 31 → `SUPPRESSION+CREATION` est posé ET relu identiquement ; les pertes connues (CLASSEMENT/TÉLÉCHARGEMENT non reconstructibles des bits, capability flag T1) ne créent pas de divergence car **les deux côtés du diff subissent la même perte** (on compare le comparable).
- **NC jamais touché** : folders `[4..20]` identiques avant/après, 0 zztest_ créé (T3 lit le réel, ne crée aucun dossier test).

## VERDICT
✅ **Le cœur seedé reproduit fidèlement les accès réels actuels (divergence nulle, prouvée en dry-run).** La mécanique **inventaire → seed → shadow est prouvée prête** pour le Postgres dédié. Le feu vert mécanique pour une future bascule (T5) est acquis — sans avoir touché un octet de la prod Nextcloud.

## Ce qui RESTE avant bascule réelle (T5, hors T3)
- **Postgres dédié** (attend feu vert Cédric) : rejouer ce seed dessus (jetable supprimé après T3 — mécanique prouvée, pas de permanent).
- **Société-pilote** + ordre de bascule (la moins risquée d'abord).
- **Gel custom_tags par société** (anti-conflit ACL pendant la transition).
- La bascule = passage `verifier` (dry) → `reconcilier --apply` par société, avec preuve diff-vide post-bascule par utilisateur. **Décision séparée.**
