# ADR-0013 — Espaces transverses

- **Statut** : proposé
- **Date** : 2026-06-15
- **Concerne** : INV-1, INV-3 ; glossaire (Dimension, Valeur de dimension, Espace,
  Espace transverse, Montage, Portée de montage)

## Contexte

La topologie des ressources se construit par combinaison de **valeurs de dimensions**
(un Espace dimensionnel = intersection de valeurs). Certains besoins légitimes ne se
rangent pas sous une seule combinaison : ils **traversent** plusieurs valeurs ou
dimensions (ex. un espace « transverse projets » partagé par plusieurs sites). Sans
construction dédiée, on serait tenté soit de dupliquer des conteneurs, soit d'attacher
des droits hors topologie — ce qui violerait INV-1 (un attribut décide OÙ et le PLAFOND,
jamais QUI).

## Décision

Introduire l'**Espace transverse** comme construction de première classe, distincte de
l'Espace dimensionnel :

1. Un Espace transverse déclare explicitement la **portée** qu'il traverse (ensemble de
   valeurs/dimensions couvertes), au lieu d'une intersection unique.
2. Il définit un **point de montage** et un **plafond de droits** (INV-1). Il ne désigne
   aucun bénéficiaire — l'attribution reste un acte humain via un groupe (INV-4).
3. Sa portée est **figée à la déclaration** (INV-3) ; un changement de topologie source
   gèle les droits existants (INV-2), il ne les recompose pas en direct.
4. L'Espace transverse reste agnostique : aucune référence à un système exécutant ; sa
   matérialisation passe par un adaptateur.

## Conséquences

- **+** Évite la duplication de conteneurs et les droits hors topologie.
- **+** La portée transverse est explicite, donc auditable par lecture (INV-6).
- **−** Modèle de données plus riche (distinguer Espace dimensionnel vs transverse).
- **−** Le calcul des plafonds doit gérer le recouvrement de portées : à spécifier en L2.

## À trancher ultérieurement

- Règle de résolution si deux espaces transverses se recouvrent sur un même montage.
- Représentation persistée de la portée (cf. L3).
