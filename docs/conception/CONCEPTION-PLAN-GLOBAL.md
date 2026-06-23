# DOC 4 — Conception : plan global (séquencement + jalons)

> Conception sur papier. Tâches implémentables APRÈS validation PM. Effort S/M/L. Méthode de preuve par tâche.
> **JALON BLOQUANT : rien n'est codé tant que le PM n'a pas validé DOC 1, 2, 3.**

## Séquence (dépendances)
```
T1 Suite de contrat ──► T2 Inbound CLI+cron ──► T3 Inventaire+Seed+Shadow ──► T5 Bascule progressive
   (verrouille            (rend opérant,           (prouve l'équivalence,         (espace par espace)
    l'agnosticité)         permet le shadow)         risque nul)                        ▲
                                                                                        │
                              T4 API + sécurité ────────────────────────────────────────┘ (après bascule)
```

| Tâche | Objectif | Dépend de | "Fait" = preuve | Effort |
|---|---|---|---|---|
| **T1 — Suite de contrat** | fake ET NC passent C1–C9 (DOC 1) | — | les 2 adaptateurs verts sur la même suite ; capability flags tracés | **M** |
| **T2 — Inbound CLI + cron** | exposer les services existants ; cron shadow + verrou | T1 (confiance port) | `sp reconcilier-tout --dry-run` tourne ; cron shadow journalise ; advisory lock testé | **M** |
| **T3 — Inventaire + Seed + Shadow** | photo ACL réelle → seed option A → shadow divergence nulle | T2 (CLI seed/dry-run) | inventaire 100% ; `reconcilier_tout --dry-run` sans `RessourceNonMappee` ; `est_conforme` sur ≥1 espace | **L** |
| **T4 — API + sécurité** | provisioning programmatique (mTLS, scopes, estampillage journal) | T2 | endpoints → service ; auth mTLS ; chaque mutation journalisée (cause+acteur+created_via) ; INV-4/5 structurels | **L** |
| **T5 — Bascule progressive** | espace par espace, neutre (option A), réversible | T3 (shadow vert) | post-bascule diff-vide par utilisateur ; rollback prouvé neutre ; journal `bascule-<espace>` | **L** (étalé) |
| **T6 (option, post-L1) — Durcissement (option B)** | enforcer le modèle fin (REFUSER, matrices, groupes perso) | T5 | changement d'accès revu utilisateur par utilisateur, tracé | **L** |

## Chemin critique
**T1 → T2 → T3 → T5.** C'est la ligne qui mène à une bascule prouvée. T3 est le plus lourd (inventaire réel + seed + atteindre divergence nulle).

## Parallélisable vs séquentiel
- **Séquentiel** (chacun débloque le suivant) : T1 → T2 → T3 → T5.
- **Parallélisable** : **T4 (API)** peut se faire en parallèle de T3, MAIS se branche/ouvre en prod **après** T5 (ne pas exposer une autorité de provisioning pendant la migration). La **rédaction de l'inventaire** (T3 lecture seule) peut commencer dès T2.
- **Prérequis infra hors-séquence** : provisionner le **Postgres dédié** possédé (décision infra) — bloque T3.

## Jalons
- **J0 — Validation PM des DOC 1-3** : déverrouille le code. (Bloquant.)
- **J1 — Agnosticité démontrée** : T1 vert sur fake+NC.
- **J2 — Cœur opérant** : T2, le shadow peut tourner.
- **J3 — Équivalence prouvée** : T3, shadow à divergence nulle sur l'espace pilote.
- **J4 — Première société basculée** : T5 sur l'espace pilote, post-bascule diff-vide.
- **J5 — Généralisation** : T5 sur toutes les sociétés.
- **(J6 — Durcissement option B)** : décision produit séparée.

## Récapitulatif des arbitrages PM (transverses aux 4 docs)
1. **DOC 1** : Nextcloud de test (conteneur jetable vs `zztest_` réel) ; seuil capability flags.
2. **DOC 2** : auth API (mTLS reco) ; API en L1 ou après bascule (reco : après) ; fréquence cron.
3. **DOC 3** : **option A (bascule neutre) vs B (durcir pendant)** [reco A] ; coexistence **(ii) par-espace** [reco ii] ; **société pilote + ordre** [besoin liste réelle] ; 2 dimensions + Projet=ressource ; qui provisionne le Postgres dédié.
4. **DOC 4** : confirmer le chemin critique T1→T2→T3→T5 et que T4 (API) vient après la bascule.

## Ce qui reste HORS de ce plan (frontière)
- L2 : transverses, montages, rôles, plafond de montage, gel-as-service, REFUSER sous-chemin (cf. CLAUDE.md §6).
- Co-accès / usage observé : hors cœur, adaptateur inbound séparé (cf. analyse-prototype §6).
- Q-téléchargement (mapping `files_accesscontrol`) : question ouverte, traçable en capability flag.
