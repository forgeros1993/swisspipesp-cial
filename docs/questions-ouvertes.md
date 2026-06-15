# Questions ouvertes & décisions du donneur d'ordre

> Journal factuel des points clarifiés ou en attente avec Cédric. Concis, à jour.

## Décidé

### D1 — « Secteur » est un ATTRIBUT, pas une dimension
L'exemple « Secteur » (Construction / Invest) de la spec était un **mauvais exemple**.
Ce n'est pas une dimension mais un **attribut** : une même société (ex. Beta) peut
relever de plusieurs secteurs sans qu'on veuille la dédoubler en espaces distincts.
Un attribut **se pose sur** un espace, il ne le **découpe** pas.

- **Conséquence** : le cas limite « deux espaces ne différant que par le Secteur » **ne
  se produit pas** dans le modèle réel.
- **Implication future** : les attributs seront probablement des **métadonnées d'espace**
  → à traiter en **L3**.

### D2 — Navigation partielle CONFIRMÉE
La navigation peut commencer par **n'importe quelle dimension** (ex. un responsable des
achats entre par le département « Achats » avant de voir les sociétés), pas forcément
par la société.

- **Conséquence** : le service de renversement actuel est **validé pour 1 et 2
  dimensions**.

## En attente

### Q1 — Renversement à 3+ dimensions / sous-dimensions = NON SPÉCIFIÉ (hors-scope)
Cédric a soulevé lui-même le cas des **sous-dimensions** (ex. Admin → RH / Compta ;
Finance → Invest / Cash-flow) et reconnaît que le concept de renversement **n'est pas
défini au-delà de 2 dimensions**. Une question de clarification lui a été renvoyée
(**plat vs hiérarchique**).

- **Décision** : tant que non tranché, on ne code **RIEN** pour le cas 3+ dimensions
  hiérarchiques. Le renversement actuel **reste tel quel**.
- **Note** : le code « nœud intermédiaire + feuilles homonymes » de `renversement.py`
  traite un cas qui **ne se produira pas** avec les dimensions réelles (puisque le
  Secteur n'est pas une dimension, cf. D1). On le **laisse en place sans s'y fier**, en
  attendant la décision de Cédric.

### Q2 — Combinaison multi-groupes (rappel)
Règle de combinaison quand un compte a plusieurs groupes (le plus permissif gagne ? un
REFUSER d'un groupe prime-t-il sur un octroi positif d'un autre ?) **non tranchée**.
`droit_effectif_compte` reste en `NotImplementedError` (voir docstring de
`core/services/droits_effectifs.py`).
