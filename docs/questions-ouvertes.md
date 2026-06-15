# Questions ouvertes & décisions du donneur d'ordre

> Journal factuel des points clarifiés ou en attente avec Cédric. Concis, à jour.

## Décidé

### D1 — « Secteur » est un ATTRIBUT, pas une dimension (interprétation de travail)
L'exemple « Secteur » (Construction / Invest) de la spec était un **mauvais exemple**.
Ce n'est pas une dimension mais un **attribut** : une même société (ex. Beta) peut
relever de plusieurs secteurs sans qu'on veuille la dédoubler en espaces distincts.
Un attribut **se pose sur** un espace, il ne le **découpe** pas.

- Cédric n'a pas tranché explicitement au dernier échange, mais **tous ses exemples
  opérationnels n'utilisent que 2 dimensions** (Société, Département) — le Secteur a
  disparu de son raisonnement de navigation, cohérent avec « Secteur = attribut ».
- **Statut** : interprétation de travail. **À confirmer si l'occasion se présente, sans
  relance dédiée.**
- **Conséquence** : le cas limite « deux espaces ne différant que par le Secteur » **ne
  se produit pas** dans le modèle réel.
- **Implication future** : les attributs seront des **métadonnées d'espace** → **L3**.

### D2 — Navigation partielle CONFIRMÉE
La navigation peut commencer par **n'importe quelle dimension** (ex. un responsable des
achats entre par le département « Achats » avant de voir les sociétés), pas forcément
par la société.

### D3 — Renversement à 2 dimensions VALIDÉ ; 3+ dimensions = HORS-SCOPE (tranché)
Cédric déclare **n'avoir aucun cas concret** justifiant une 3ème dimension de
renversement (« j'ai aucun exemple qui montre qu'il y a un besoin… pas de cas concret
qui émerge »). Le **modèle plat + ordre de dimensions** (Société → Département, 2
dimensions) couvre **100 % de ses besoins réels**.

- Son exemple (Alpha/Beta/Gamma, départements partiellement communs
  Finance/Admin/Technique/SAV/Projet ; entrer par Finance → toutes les sociétés ayant
  Finance ; entrer par Technique → les sociétés ayant Technique) correspond **exactement**
  au service `renverser` déjà codé.
- **Décision** : on **ne construit PAS** le cas hiérarchique 3+ / sous-dimensions
  (anciennement Q1). 2 dimensions suffisent. Le renversement actuel **reste tel quel**.
- **Note** : le code « nœud intermédiaire + feuilles homonymes » de `renversement.py`
  traite un cas qui **ne se produira pas** avec les dimensions réelles (cf. D1). Laissé
  en place **sans s'y fier**.

### D4 — Vigilance agnosticité (rappel de discipline)
Cédric a dérivé un instant vers « l'espace = héritage Nextcloud / tags dans des Shared
Folders » avant de reconnaître qu'il allait trop loin. **Rappel** : le cœur reste
**agnostique** — un espace est un **croisement abstrait de coordonnées**, Nextcloud
n'est **qu'un adaptateur**. C'est la discipline du garde-fou de pureté, **déjà en place**
(`test_core_purity.py` + ruff TID251). Pas d'action de code.

## En attente

### Q2 — Combinaison multi-groupes
Règle de combinaison quand un compte a plusieurs groupes (le plus permissif gagne ? un
REFUSER d'un groupe prime-t-il sur un octroi positif d'un autre ?) **non tranchée**.
`droit_effectif_compte` reste en `NotImplementedError` (voir docstring de
`core/services/droits_effectifs.py`).
