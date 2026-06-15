# ADR-0014 — Journal d'accès

- **Statut** : proposé
- **Date** : 2026-06-15
- **Concerne** : INV-2, INV-5, INV-6 ; glossaire (Journal d'accès, Rôle, Groupe)

## Contexte

L'audit des accès doit répondre « qui pouvait quoi, quand, et pourquoi » sans recalcul.
Deux exigences se croisent : INV-6 (l'audit se **lit**, il ne se calcule pas) et INV-2
(un changement de topologie **gèle** les droits — sortis de l'état courant et **tracés**,
jamais détruits). Il faut une trace qui survive aux changements et ne soit jamais
réécrite.

## Décision

Tenir un **Journal d'accès append-only**, séparé de l'**état courant** :

1. **Append-only strict** : insertions seules. Aucune update, aucune delete sur le
   journal (garde-fou applicatif + absence de chemin d'écriture destructif). Cohérent
   avec INV-5 (l'API n'efface jamais).
2. **Deux représentations** : l'**état courant** (droits effectifs au présent, mutable
   par archivage/remplacement) et le **journal** (historique immuable des décisions et
   des **gels**).
3. **Le gel écrit au journal** (INV-2) : quand une donnée source change la topologie, le
   droit est retiré de l'état courant et un événement de gel est **ajouté** au journal,
   jamais une réactivation automatique.
4. Chaque entrée porte au minimum : identifiant (UUID), horodatage UTC, type
   d'événement (pose / gel / archivage / remplissage de rôle), ressource, groupe et
   rôle concernés, et la décision figée. **Jamais le nom d'une personne** (INV-4, INV-5)
   — uniquement des groupes.
5. **L'audit est une lecture** de (état courant ∪ journal), sans recomputation des
   droits (INV-6, INV-3).

## Conséquences

- **+** Audit déterministe et reproductible par simple lecture.
- **+** Les gels sont traçables et réversibles seulement par acte humain explicite.
- **−** Croissance monotone du journal : prévoir indexation / archivage froid (hors gel
  fonctionnel).
- **−** Discipline d'écriture à garantir au niveau persistance (pas de cascade delete,
  pas de trigger destructif) — à câbler en L3.

## À trancher ultérieurement

- Schéma exact des entrées et index (L3).
- Politique de rétention du journal froid (sans jamais supprimer un gel actif).
