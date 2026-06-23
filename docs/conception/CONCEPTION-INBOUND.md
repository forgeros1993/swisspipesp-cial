# DOC 2 — Conception : point d'entrée inbound (CLI + cron + API)

> Conception sur papier. Aucun code. `adapters/inbound/` est aujourd'hui un stub vide (`.gitkeep` + `__init__` vide).
> Règle d'or : **l'inbound n'a AUCUNE logique métier**. Il authentifie, valide la forme, et appelle les services applicatifs **déjà codés** (`reconcilier_ressource`, `reconcilier_tout`, `verifier_et_reconcilier`, `RapportReconciliation`). Toute mutation → **journal append-only avec cause**.

## Schéma (2.d)
```
   CLI (admin/expl)   CRON (continu)        API (programmatique)
        \                 |                      /
         \                |                     /
          v               v                    v
        ┌─────────────────────────────────────────┐
        │  COUCHE SERVICE (existe déjà)             │
        │  verifier_et_reconcilier / reconcilier_*  │
        └─────────────────────────────────────────┘
                          │
                          v
                ┌───────────────────┐
                │  CŒUR (pur)       │  droits_effectifs, reconciliation,
                │  + PORT           │  renversement  →  AdaptateurRessource
                └───────────────────┘
                          │
                          v
                 Adaptateur Nextcloud (occ/SSH + SQL lecture)
                          │
                  + JOURNAL append-only (chaque mutation, cause)
```

## 2.a — CLI (admin / exploitation)
Outil de l'admin **et** brique réutilisée par le cron. Toutes les commandes appellent la couche service.

| Commande | Args | Appelle | Sortie |
|---|---|---|---|
| `sp reconcilier-espace <ressource_id> [--apply\|--dry-run]` | id interne | `reconcilier_ressource` | divergence + (réparé\|détecté) |
| `sp reconcilier-tout [--apply\|--dry-run]` | — | `reconcilier_tout` | `RapportReconciliation` (total/conformes/réparées/erreurs) |
| `sp appliquer <ressource_id>` | id | service → `appliquer_droits` (état désiré recalculé) | ok/erreur |
| `sp divergences [--espace X]` | filtre | `comparer_droits` en lecture | liste des écarts (groupes manquants/en trop/matrices divergentes) |
| `sp lire-effectifs <ressource_id>` | id | `lire_droits_effectifs` | état réel par groupe |
| `sp seed-depuis-nc <espace> --dry-run` | espace | inventaire (DOC 3) | photo de l'ACL réelle → proposition de seed (n'écrit pas) |
| `sp journal <ressource_id\|--depuis DATE>` | filtre | lecture journal | entrées append-only (cause, acteur, horodatage) |

Défaut **`--dry-run`** sur tout ce qui mute (apply explicite requis). `--apply` exige une **cause** (`--cause "…"`) inscrite au journal.

## 2.b — CRON / réconciliation continue
Branche `verifier_et_reconcilier` sur une exécution périodique (le TODO de CLAUDE.md §9).
- **Fréquence** : configurable ; reco horaire pour la détection de dérive (upgrade NC = symptôme #3246), + déclenchement sur détection d'upgrade NC (déjà conçu, `etat_systeme`/`_doit_reconcilier`).
- **Quoi** : `reconcilier_tout` (balayage résilient existant — une ressource en erreur ne bloque pas les autres).
- **Mode** : **paramétrable `dry-run` vs `apply`** — central pour la bascule : pendant la transition, le cron tourne en **SHADOW (dry-run)** = il DÉTECTE et journalise les divergences, **n'écrit rien**. On ne passe en `apply` qu'espace par espace, une fois la bascule de cet espace validée (DOC 3).
- **Idempotence / concurrence** : deux runs ne doivent pas se chevaucher → **verrou** (advisory lock Postgres `pg_advisory_lock`, ou un marqueur `etat_systeme` "reconciliation_en_cours" + timeout). Reco : advisory lock PG (atomique, libéré en fin de transaction).
- **Logs/alertes** : toute divergence en mode shadow → log + (option) alerte ; en mode apply → réparation tracée au journal avec cause `cron-reconciliation`.

## 2.c — API (FastAPI) — la sécurité AU CENTRE
Une API qui **pose des droits EST une autorité de provisioning** : qui peut l'appeler peut ouvrir n'importe quel accès. Secret de premier rang. La sécurité n'est pas une annexe.

**Endpoints** (tous → couche service, jamais de logique) :
| Méthode | Endpoint | Rôle |
|---|---|---|
| `POST` | `/ressources` | créer une ressource (→ `creer_ressource`) |
| `POST` | `/ressources/{id}/archiver` | archiver (réversible, **jamais delete**) |
| `PUT` | `/ressources/{id}/droits` | poser l'**état désiré complet** (idempotent) |
| `GET` | `/ressources/{id}/droits-effectifs` | lire l'état réel |
| `POST` | `/reconciliation` | déclencher (dry-run/apply) |
| `GET` | `/journal` | lire le journal append-only |

**Authentification** : service-to-service. Options : (a) **token signé** (JWT court, scope restreint) ; (b) **mTLS** (le plus fort, certificats mutuels). Reco : **mTLS** entre services internes + token scoping pour distinguer les appelants ; pas d'auth "user/password".
**Autorisation** : qui peut quoi — un appelant "lecture" (GET droits/journal) ≠ un appelant "provisioning" (PUT droits). Scopes : `lecture`, `reconciliation`, `provisioning`. Le provisioning est le scope le plus sensible.
**Estampillage d'origine (INV-6)** : CHAQUE mutation via API inscrit au journal `created_via` (api/cli/cron), l'**acteur** (le service appelant authentifié) et la **cause**. On doit toujours pouvoir répondre "qui a décidé ce droit". Pas de mutation anonyme.
**Garde-fous structurels (INV-4/INV-5)** : l'API **ne peut jamais** (a) nommer une personne en direct — elle ne manipule que des **groupes** (le modèle n'a pas de champ user, c'est structurel) ; (b) supprimer — `archiver` seulement, jamais delete dur. Le DTO d'entrée n'expose aucun chemin pour ces deux actes.

## Points communs (2.d)
Les 3 portes → **même couche service** → cœur → port → adaptateur. **Toute mutation → journal append-only (cause obligatoire)**. Aucune des 3 ne contient de règle de droits (elles exposent un cœur qui décide).

## Arbitrage PM
- **Auth API** : mTLS (fort, plus d'ops) vs token JWT (simple, moins fort). Reco mTLS pour le provisioning.
- **Faut-il l'API en L1 ?** La bascule (DOC 3) n'a besoin que de **CLI + cron** (shadow). L'API peut venir APRÈS la bascille (séquence DOC 4). Reco : CLI+cron d'abord, API ensuite (elle ouvre une surface d'attaque qu'on ne veut pas pendant la migration).
- **Fréquence cron** + politique d'alerte sur divergence.
