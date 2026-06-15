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

### D5 — Mode REFUSER CONFIRMÉ (pas une coquille)
Cédric s'interrogeait sur l'origine du REFUSER. **Confirmé** : présent dans sa spec
(§9.2 « Refuser : liste noire explicite, prioritaire sur l'héritage »), le glossaire et
le modèle §4.6. Rôle : **exception d'héritage** — cacher un sous-dossier précis malgré
l'héritage du parent. **On le garde** (implémenté dans `droit_effectif_groupe`).

### D6 — Combinaison multi-groupes : modèle ADDITIF (tranché)
Le droit positif **le plus permissif gagne** à travers l'ensemble des groupes d'une
personne. Un **REFUSER ne bloque l'accès QUE via le groupe sur lequel il porte** ; si la
personne a un autre groupe qui donne l'accès, elle l'obtient. Le REFUSER agit **à
l'intérieur d'un groupe** (coupe l'héritage pour ce groupe) ; **entre les groupes**, on
prend le **maximum des droits positifs** — un REFUSER n'écrase jamais le positif d'un
autre groupe.

- **Exemple Cédric** : dossier « Salaires direction » REFUSER pour le groupe Finance,
  mais hérité/positif pour Direction. Marie (Finance ET Direction) **voit** le dossier
  via Direction.
- **Implémenté** : `droit_effectif_compte` (union des droits : niveau max + additionnels
  unis). Voir `core/services/droits_effectifs.py` et `Matrice.fusionner`.

### D7 — Le classement ne DÉTRUIT pas (tranché)
Cédric : « reclasser et détruire ne doivent **pas** être les mêmes droits ». Le mapping
actuel `CLASSEMENT = create|delete` **sur-octroie** un pouvoir de suppression → à
corriger. Décision : le classement **ne doit pas embarquer la capacité de détruire**.
**Action différée** à la tranche `files_accesscontrol` (le `delete` devra être restreint
côté Nextcloud). Tranché ; pas d'action de code dans le cœur.

## En attente

### Q-téléchargement — Mapping Nextcloud du droit TÉLÉCHARGEMENT
Toujours ouvert. Pas de bit "download" distinct dans Group Folders (le download suit
`read`). → **non mappé** (0 bit) pour l'instant, à traiter via `files_accesscontrol` à
la tranche réseau. Pas de faux bit inventé. (Décision d'adaptateur, hors cœur.)
