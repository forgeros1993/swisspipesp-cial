# Cartographie du prototype `custom_tags` — référence de refonte

> Pont prototype → cible. Ce document fige ce que fait le prototype et ce que la
> refonte **garde / corrige / déplace**. Ce n'est PAS une doc de `custom_tags` pour
> lui-même.

## Préambule

- **`custom_tags`** : application Nextcloud développée à la main, **en production** sur
  `nx.vellis.ch` (Nextcloud 33, préfixe DB réel **`hynp_`**). Gère fichiers + tags
  multi-société. Fonctionne, mais accumule incohérences et couplages.
- **`swisspipesp-cial`** : refonte propre (architecture hexagonale, cœur agnostique)
  destinée à **le remplacer**.
- Savoir extrait par **lecture du code** (snapshot rapatrié, mai→juin 2026) + lecture
  directe en base pour les 2 tables hors-migration. Aucune modification du prototype.

## 1. Modèle de données du prototype

Six tables `custom_tags_*`. Quatre créées par migrations (V1.1→V1.3), **deux
(`companies`, `master_sessions`) absentes des migrations** — lues directement en base
(créées hors-migration).

| Table | Schéma résumé | Rôle métier |
|---|---|---|
| **hierarchy** | `id` PK ; `parent_id` (null) ; `category_type` str(64) ; `name` str(64) ; `systemtag_id` int déf 0 | **Topologie** : arbre de nœuds (parent_id), chaque nœud = un SystemTag Nextcloud. `category_type` ∈ `racine`/`societe`/… |
| **permissions** | `id` PK ; `node_id` ; `principal_type` (user/group) ; `principal_id` ; `access_level` ; `can_download` int déf 0 ; `can_reclassify` int déf 0 ; `expires_at` (null) ; `granted_by` (null) | **Droits posés** : qui a quel niveau sur quel nœud, + additionnels, + expiration, + traçabilité. |
| **settings** | `setting_key` PK str(64) ; `setting_value` str(255) | Config clé/valeur (dont `master_password`, `co_access_last_run_ts`). |
| **co_access** | `id` PK ; `folder_path` str(512) ; `company_id` ; `company_name` ; `folder_name` ; `user_a` ; `user_b` ; `started_at`/`ended_at` bigint ; `event_count` ; `modifications_json` text ; `detected_at` bigint ; **UNIQUE**(folder_path,user_a,user_b,started_at) | **Audit de co-accès** : fenêtres où 2+ users modifient le même dossier. |
| **companies** *(hors-migration)* | `id` PK ; `name` str(255) ; `slug` str(64) **UNIQUE** ; `password_hash` str(255) déf '' ; `group_id` str(255) déf '' ; `folder_id` int (null) ; `responsible_uid` str(64) (null) ; `created_at` int déf 0 | **Sociétés** : société ↔ groupe NC ↔ group folder ↔ responsable. `slug` = identité unique. |
| **master_sessions** *(hors-migration)* | `uid` PK str(255) ; `expires_at` bigint | **Élévation temporaire** : 1 session master max par user. |

## 2. Comment le prototype décide « qui peut quoi »

**Deux systèmes de niveaux qui ne se parlent pas** — c'est le défaut central.

- **Système A — `getCurrentUserLevel(): int`** (gouverne TOUS les gates) :
  `admin → 999` ; appartenance groupe NC `custom_tags_level_3/2/1 → 3/2/1` ;
  responsable → 2 ; sinon 0. **Aucune notion de 1.5.** Pilote `canSeeHierarchy()` (≥2),
  `canSeeAdministration()` (≥3), `canManageNodePermissions()` (≥2).
- **Système B — `$effectiveLevel: float`** (calculé **inline** dans `fileAuditApi` et
  `coAccessApi`) : admin/level3 → 3 ; responsable → 2 ; **sinon, si l'user a ≥1
  permission explicite → `1.5`**, sinon 1. Gate audit = `effectiveLevel >= 1.5`.

**Le bug du 1.5** : un user « 1.5 » (niveau 1 + permissions explicites) **peut** voir
l'audit (système B), mais `getCurrentUserLevel()` le voit comme **1** → il **ne peut
pas** voir la hiérarchie ni gérer de permissions. Le « 1.5 » n'existe que dans 2
endpoints, jamais remonté dans le système A. Les deux notions divergent.

Aggravant : la requête du 1.5 compte `access_level IN ('read','edit','write','manage','full')`
alors que `setPermission` n'accepte que `('read','write','full','ghost')` → `edit`/`manage`
sont morts et **`ghost` est exclu** du comptage.

**Résolution des droits dans `auditUser()`** — deux sources fusionnées par nœud :
- **explicite** (`custom_tags_permissions` où `principal_id=uid`) : `access_level`,
  `can_download`, `can_reclassify` tels quels ; `can_delete = (access_level==='full')`.
- **implicite** (nœud d'une société autorisée, sans permission explicite) : défaut
  `write` ; admin/level3/responsable → `full`+delete.
- **Règle : explicite > implicite** ; appartenance société = `write` implicite.

**Pas d'héritage arbre réel** : `grantUserLevelAccess()` pose la **même** ligne de
permission sur **tous** les nœuds d'une société d'un coup (duplication), au lieu d'un
héritage calculé parent→enfant. Chaque nœud porte sa propre ligne.

## 3. Les 4 canaux Nextcloud

Le prototype parle à Nextcloud par **quatre voies**, aucune via une API propre :

1. **`occ` CLI en `shell_exec`/`exec`** : `groupfolders:create`,
   `groupfolders:group <id> <grp> write delete share`, `groupfolders:delete`,
   `groupfolders:scan`.
2. **Filesystem direct** : `mkdir`/`rename`/`unlink` sous
   `<datadir>/__groupfolders/<folder_id>/files/`.
3. **SQL brut** sur tables Nextcloud : `oc_group_folders`, `oc_group_folders_groups`,
   `oc_group_folders_acl` (perms `0=none/1=read/else=write`), `oc_filecache`,
   `oc_storages`, `oc_activity`.
4. **API OCP propre** : `IGroupManager`, `IUserManager`, `ISystemTagManager`,
   `INotificationManager`.

**Socle réel = Group Folders + SystemTags.** La topologie est matérialisée en
SystemTags (`hierarchy.systemtag_id`) ; les dossiers d'équipe sont des Group Folders ;
les droits fins passent par les ACL Group Folders et la table `permissions` maison.

## 4. Correspondance prototype → cœur SwissPipe

| Concept prototype | Cible cœur SwissPipe | Statut |
|---|---|---|
| `access_level` read ⊂ write ⊂ full | `Matrice` + `NiveauPrincipal` (Lecture⊂Écriture⊂Suppression) | **GARDÉ** (réifié proprement) |
| `ghost` (visibilité seule, deny) | Mode **REFUSER** (`Octroi`, `DroitEffectif.bloque`) | **GARDÉ** |
| `can_download` | Additionnel **TÉLÉCHARGEMENT** | **GARDÉ** (mapping NC = question ouverte) |
| `can_reclassify` | Additionnel **CLASSEMENT** | **GARDÉ** (D7 : ne doit pas détruire) |
| `expires_at` sur permission | **Gel / expiration** des droits (INV-2) | **GARDÉ**, redéfini en gel tracé |
| `granted_by` | **Journal d'accès** append-only (ADR-0014, INV-6) | **GARDÉ**, déplacé vers le journal |
| `permissions` (mutable, UPDATE/DELETE) | État courant `octroi` + journal append-only | **CORRIGÉ** (append-only, INV-6) |
| niveaux user via groupes `custom_tags_level_*` | Rôles / groupes du domaine | **CORRIGÉ** (un seul calcul) |
| `1.5` inline | — | **SUPPRIMÉ** (calcul de droits unique) |
| `companies` (société↔groupe↔folder) | `Espace` dimensionnel (dimension Société) + `Groupe` + `ressource_mapping` | **DÉPLACÉ/REMODELÉ** |
| `master_session` (mute les groupes) | Élévation temporaire **sans** muter les groupes | **CORRIGÉ** (INV-4/5) |
| group folder ↔ chemin physique/`folder_id` | `ressource_mapping` (interne↔externe) | **DÉPLACÉ** hors cœur (adaptateur) |
| topologie = SystemTags | `EspaceDimensionnel` = croisement abstrait de coordonnées | **CORRIGÉ** (cœur agnostique, D4) |
| co-accès (`oc_activity`) | Usage observé | **DÉPLACÉ** hors cœur (voir §6) |

## 5. Ce que la refonte corrige

- **Un seul calcul de droits effectifs** (`droit_effectif_groupe` + `droit_effectif_compte`)
  au lieu de deux systèmes incohérents et du 1.5 fantôme.
- **Héritage calculé** parent→enfant (cascade + modes) au lieu de lignes de permission
  dupliquées sur chaque nœud.
- **Combinaison multi-groupes additive** explicite (le plus permissif gagne, REFUSER
  intra-groupe) au lieu de règles implicites éparpillées.
- **Master session = élévation temporaire SANS mutation de groupes** (le cœur ne nomme
  jamais une personne dans un groupe pour donner un accès — INV-4/INV-5).
- **Pas de backdoor** (le prototype a `isAdminUser()` qui matche « johan », et un param
  `?backdoor`).
- **Pas de secrets en dur** (le prototype avait 2 mots de passe défaut en constantes).
- **Journal append-only** (trigger Postgres anti UPDATE/DELETE) au lieu d'`UPDATE`/`DELETE`
  directs sur `permissions`.
- **Agnosticité via port `AdaptateurRessource`** au lieu de `shell_exec occ` + filesystem
  + SQL brut sur tables Nextcloud.

## 6. Ce qui sort du cœur

**La détection de co-accès** (`DetectCoAccess` + table `co_access`) lit `oc_activity`
= **usage réel observé**, pas des droits posés. C'est de la **surveillance/analytique**,
pas de la gouvernance.

- → **capacité produit séparée**, via un **adaptateur inbound** qui consomme les events
  d'un exécutant, jamais dans le cœur de décision.
- Distinction structurante pour la refonte : **décisions** (qui peut quoi → journal
  append-only, INV-6) **vs usage observé** (qui a fait quoi → flux d'activité). Le cœur
  ne mélange pas les deux.

## 7. Pièges techniques Nextcloud / Infomaniak (pour l'adaptateur)

Confirmés pendant la reconnaissance ; à anticiper dans la tranche réseau de l'adaptateur :

- **`occ db:execute` absent** (sur cette version) ; `migrations:execute` et
  `background-job:add` également indisponibles → pas de raccourci CLI pour SQL/jobs.
- **`getDatabaseConnection()` → `ConnectionAdapter`** : l'objet retourné n'est pas un
  PDO brut, attention aux méthodes de fetch (`fetchAssociative`/`fetch`/`fetchAll`
  selon le ResultAdapter — le prototype a un helper de compat).
- **`node` non exécutable** par l'utilisateur SSH (`bq9g0s_johan`) → pas de build front
  côté serveur.
- **`set +H`** avant de coller des commandes contenant `!` (history expansion bash).
- **Rename DAV = MOVE = besoin de la permission Delete** : un Group Folder doit avoir le
  masque **31** (read+update+create+delete+share) sinon le renommage/déplacement casse.
- **Préfixe DB réel = `hynp_`** (pas `oc_`) : toujours lire `dbtableprefix`, jamais
  coder le préfixe en dur.
- **Limite d'index MySQL utf8mb4 = 767 bytes** : colonnes indexées ≤ 191 caractères
  (varchar(191)) pour rester sous la limite.

---

*Référence figée. Source : lecture du snapshot `custom_tags` (prod `nx.vellis.ch`),
mai→juin 2026. Mots de passe du prototype redacted.*
