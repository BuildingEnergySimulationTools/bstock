# bstock — Spécification

> Statut : **v3.0**. v2 implémentée par bstock 0.3.0 (périmètres v1 et v2 du §12 : modèle, provenance,
> compteurs, persistance ; emprise §3.6, connecteurs §8, exports §9, annexe A). Issue des interviews de
> conception du 2026-10-13 et ajustée pendant l'implémentation. Points ouverts : §13.
>
> **v3** (interview du 2026-10-15), implémentée par bstock 0.4.0 à 0.8.0 (§12) : lisibilité, sélection,
> analyse et graphiques (§14), consommations mesurées (§6.5), métadonnée `extensive` (§3.3).

---

## 1. Objectif

`bstock` est une bibliothèque Python qui fournit une **structure de données commune** pour décrire un
parc de bâtiments, sa provenance et ses données mesurées.

### Utilisateurs
- **Autres libs et outils internes** : bstock sert de modèle de données pivot (simulation, analyses, outils
  métier).
- **Ingénieurs et data scientists** : manipulation en notebook.

### Cas d'usage
1. **Inventaire / patrimoine** : recenser sites et bâtiments, leurs caractéristiques et leurs usages.
2. **Simulation énergétique** : décrire enveloppe et systèmes pour alimenter des modèles thermiques.
3. **Données mesurées** : rattacher des compteurs et leurs séries temporelles aux éléments du parc, au format
   [tide](https://github.com/BuildingEnergySimulationTools/tide).

### Principes
- **Un socle commun, étendu par projet.** Les attributs issus des référentiels (RNB, BDNB, DPE) sont dans le
  socle. Les attributs propres à un client (Ville de Bayonne, Ville de Bordeaux…) sont ajoutés par
  héritage dans le code du projet.
- **Traçabilité.** Chaque valeur descriptive sait d'où elle vient. Plusieurs sources peuvent coexister, et
  une règle de priorité choisit la valeur effective.
- **Simple à l'usage.** `building.construction_year` renvoie directement une valeur ; la provenance est
  consultable à part.

### Hors périmètre
- Zones thermiques, étages et locaux : le bâtiment est la maille la plus fine.
- Lecteurs de fichiers clients (xlsx, csv) : ils sont écrits dans chaque projet, hors de bstock.
- Traitement des séries temporelles (nettoyage, rééchantillonnage) : c'est le rôle de tide.
- Simulation énergétique elle-même.

---

## 2. Concepts

| Concept | Rôle |
|---|---|
| **BuildingPark** | Racine. Contient les sites, les bâtiments hors site, les compteurs et la configuration de provenance. |
| **Site** | Regroupement **optionnel et imbriquable** de bâtiments : site métier du client (groupe scolaire, campus…) ou groupe BDNB (`batiment_groupe`). Porte les valeurs communes à ses bâtiments, qui en héritent (§4.4), et des systèmes partagés. |
| **Building** | Un bâtiment au sens du **RNB** : 1 Building ↔ au plus 1 `rnb_id`. Le RNB est le pivot vers la BDNB et les DPE. |
| **System** | Équipement : production de chauffage, de froid, d'ECS, ventilation, PV. Rattaché à un bâtiment ou à un site (partagé). |
| **Envelope** | Enveloppe d'un bâtiment ou d'un site : synthèse et parois optionnelles. |
| **Meter** | Point de mesure (compteur, sous-compteur, capteur) qui concerne 1..n entités du parc. |
| **DataReader** | Description de l'endroit où lire les données d'un Meter (CSV, Parquet, API…). |
| **SourcedValue** | Une valeur d'attribut accompagnée de sa source, de sa date et d'un commentaire. |
| **Source** | Identifiant de l'origine d'une information (`"bdnb"`, `"dpe"`, `"audit"`, `"bayonne_xlsx_2024"`…). |

---

## 3. Modèle de données

### 3.1 Vue d'ensemble

```
BuildingPark
 ├─ sites: list[Site]
 │   ├─ sites: list[Site]              # sous-sites, profondeur libre
 │   ├─ buildings: list[Building]
 │   ├─ envelope: Envelope             # valeurs communes (héritées si intensives)
 │   └─ systems (partagés) : heating / cooling / dhw / ventilation / pv
 ├─ buildings: list[Building]          # bâtiments rattachés directement au parc
 │   ├─ envelope: Envelope
 │   └─ systems : heating / cooling / dhw / ventilation / pv
 ├─ meters: list[Meter]                # scope -> ids d'entités (site, bâtiment, système)
 │   └─ reader: DataReader | None
 └─ source_priority: SourcePriority
```

Toutes les entités (`Site`, `Building`, `Envelope`, systèmes, `Meter`) héritent de
`SourcedModel`, qui fournit le mécanisme de provenance (§4).

### 3.2 Identifiants

- Toute entité référençable a un `id: str` **unique dans le parc** :
  - `Building.id` : id interne **stable**, égal au `rnb_id` s'il est connu à la création, généré sinon
    (`"bldg-1f3a"`) ou choisi par l'utilisateur. Il ne change jamais, même si le `rnb_id` est corrigé plus
    tard : `scope`, `serves` et les tags tide restent valides ;
  - `Site.id` : choisi par l'utilisateur (code client, par exemple) ;
  - systèmes et Meters : choisis par l'utilisateur ou générés automatiquement (préfixe + 8 caractères
    hexadécimaux, ex. `"heating-1f3a9c0b"`) ;
  - parois d'enveloppe : même règle, mais l'unicité est vérifiée au sein de leur enveloppe seulement.
- Les ids sont **immuables** une fois l'entité créée.
- Les ids ne doivent pas contenir `__`, qui sert de séparateur aux tags tide. Cette règle est vérifiée à
  la création.
- `park.get(id)` renvoie l'entité correspondante ; `park.get_by_rnb(rnb_id)` retrouve un bâtiment par son
  identifiant RNB.
- `rnb_id` est optionnel (bâtiment neuf, pas encore apparié) mais **unique dans le parc** quand il est
  renseigné. Il est modifiable.
- Formats des identifiants externes (validation légère : un format inattendu émet un avertissement, pas
  une erreur) :
  - RNB : 12 caractères alphanumériques (ex. `8K3CTZKZ9R27`) ;
  - BDNB : `bdnb-bg-XXXX-XXXX-XXXX` pour un groupe, `bdnb-bc-XXXX-XXXX-XXXX` pour une construction ;
  - DPE : 13 caractères (ex. `2611E0031228S`).

### 3.3 Conventions

- **Unité fixée par champ**, sans bibliothèque d'unités : SI, ou unité usuelle du métier telle que la
  donnent les sources (kW, kWp, L, °, m³/(h.m²)), pour éviter des conversions dans les connecteurs. L'unité
  est déclarée dans les métadonnées du champ : elle apparaît dans le JSON Schema, dans `Building.sourced_fields()["heated_area"].unit` et dans
  le tag `unit` de tide.
- Les attributs **descriptifs** sont « sourcés » (§4). Les **identifiants et relations** (`id`, `rnb_id`,
  listes d'enfants, `scope`, `serves`) sont des valeurs simples.
- Tous les attributs sourcés sont optionnels : une valeur inconnue vaut `None`.
- **Grandeurs extensives** (v3) : un attribut qui s'additionne d'une entité à l'autre (comptage,
  surface, puissance, coefficient de déperdition) est déclaré `sourced(..., extensive=True)`. C'est la
  métadonnée qu'utilisent les agrégations (§14.3) et l'indicateur kWh/m² (§6.5). Elle est distincte de
  `inherit=False` : `name` et `footprint` ne s'héritent pas non plus, mais ne s'additionnent pas. Un champ
  ne peut pas être à la fois `extensive=True` et `inherit=True` (`InvalidArgumentError` à la définition
  de la classe). Dans le socle, sont extensifs : les surfaces et `dwelling_count` (§3.6), `loss_area` et
  `heat_loss_coefficient` de l'enveloppe (§3.7), `nominal_power` des générateurs et `peak_power` du PV
  (§3.8). La métadonnée figure dans `sourced_fields()` et dans le JSON Schema (`"extensive"`). Une sous-classe projet déclare ses propres champs extensifs de la même façon.
- **Les collections (sites, bâtiments, systèmes, parois, compteurs) se modifient uniquement via les
  méthodes du parc ou des entités** (`new_*`, `add_*`, `remove`, `move`). Ces méthodes maintiennent l'index
  des ids, les liens vers le parent et les contrôles de cohérence. La modification directe des listes
  est refusée : `park.sites`, `park.buildings`, `park.meters`, `site.sites`, `site.buildings`, les
  listes de systèmes (`heating`… `pv`) et `envelope.components` restent des `list` en lecture (index,
  itération, `==`), mais leurs méthodes de modification (`append`, `remove`, `sort`, `lst[i] = …`…) et
  leur réaffectation (`site.buildings = [...]`) lèvent `HierarchyError` en citant les méthodes à
  utiliser. Les listes d'ids (`scope`, `serves`, `dpe_ids`) ne sont pas concernées (§3.8).

### 3.4 BuildingPark

| Attribut | Type | Description |
|---|---|---|
| `name` | `str` | Nom du parc (ex. « Ville de Bayonne ») |
| `description` | `str \| None` | |
| `sites` | `list[Site]` | Sites de premier niveau |
| `buildings` | `list[Building]` | Bâtiments rattachés directement au parc (sans site) |
| `meters` | `list[Meter]` | |
| `source_priority` | `SourcePriority` | Règles de résolution (§4.3) |
| `site_cls` | `type[Site]` | Classe utilisée pour créer et charger les sites (non sérialisée, §5) |
| `building_cls` | `type[Building]` | Idem pour les bâtiments |

Méthodes principales :

| Méthode | Rôle |
|---|---|
| `new_site(id, *, parent=None, source=None, **attrs)` | Crée un site (instance de `site_cls`) au premier niveau ou sous `parent` |
| `new_building(rnb_id=None, *, id=None, parent=None, source=None, **attrs)` | Crée un bâtiment (instance de `building_cls`) |
| `add_site(site, parent=None)`, `add_building(building, parent=None)` | Ajoute un objet déjà construit (contrôle de classe et d'unicité des ids) |
| `add_system(system, owner)` | Ajoute un système à un bâtiment ou à un site (`owner` = objet ou id) |
| `add_meter(meter)` | Ajoute un compteur ; les ids du `scope` doivent exister |
| `get(id, cls=None)`, `get_by_rnb(rnb_id)` | Recherche (sites, bâtiments, systèmes et compteurs) ; renvoie `None` si absent. Avec `cls` (ex. `Building`), le type renvoyé est restreint et une entité d'une autre classe lève `UnknownIdError`. `park[id]` lève `UnknownIdError`, `id in park` teste la présence |
| `iter_sites(recursive=True)`, `iter_buildings(recursive=True)`, `iter_systems()` | Parcours de tout l'arbre, ou du premier niveau seulement avec `recursive=False` |
| `move(entity, new_parent, force=False)` | Déplace un site ou un bâtiment (objet ou id ; `new_parent` = site, ou `None` pour le premier niveau). Lève `EntityInUseError` si un système d'un site quitté dessert un bâtiment déplacé (`serves`), sauf avec `force=True`, qui retire ces bâtiments du `serves` |
| `remove(entity, force=False)` | Supprime une entité, objet ou id (un site avec tout son contenu). Lève une erreur si elle est référencée (`scope` d'un Meter, `serves` d'un système), sauf avec `force=True`, qui retire aussi les références |

> Changement par rapport au draw.io : `municipality`, `country`, `lat` et `lon` sont déplacés vers
> Site et Building. Un parc peut couvrir plusieurs communes.

### 3.5 Site

Un site est un regroupement **optionnel** et **imbriquable** :
- **site métier** : défini par le client (groupe scolaire, campus, résidence…) ;
- **groupe BDNB** : un `batiment_groupe` qui contient plusieurs bâtiments RNB. La BDNB décrit l'essentiel
  (usage, année, logements, DPE représentatif) au niveau du groupe, pas de la construction RNB. Ces
  valeurs sont donc rangées sur un site, et les bâtiments en héritent.

Les deux se combinent : `Site « Groupe scolaire A » > Site « bdnb-bg-… » > Buildings`.

| Attribut | Type | Description |
|---|---|---|
| `id` | `str` | Identifiant unique (code client, ou `batiment_groupe_id` pour un groupe BDNB) |
| `bdnb_batiment_groupe_id` | `str \| None` | Renseigné si le site correspond à un groupe BDNB |
| `dpe_ids` | `list[str]` | DPE qui portent sur le site (ex. DPE immeuble) |
| `sites` | `list[Site]` | Sous-sites |
| `buildings` | `list[Building]` | Bâtiments rattachés directement au site |
| `envelope` | `Envelope` | Enveloppe commune (ex. Ubat d'un DPE immeuble) |
| `heating`, `cooling`, `dhw`, `ventilation`, `pv` | `list[...]` | Systèmes partagés (§3.8) |
| *attributs descriptifs* | | Les attributs communs du §3.6 |

Accès dérivés (non sérialisés) : `site.parent` (site parent ou `None`, comme `building.parent`), `site.park`,
`site.iter_buildings(recursive=True)`, `site.iter_sites(recursive=True)`.

Gestion des enfants au niveau du site : `site.add_site(s)`, `site.add_building(b)`, `site.detach(id)`. Site
et Building ont aussi `add_system(system)` (rangé dans la liste de son `kind`), `remove_system(id)` et
`iter_systems(kind=None)`. Quand l'entité appartient à un parc, ces méthodes mettent à jour l'index des ids
du parc. `detach` et `remove_system` font sortir l'entité du parc avec les mêmes contrôles que
`park.remove` : elles lèvent `EntityInUseError` si l'entité ou son contenu est référencé hors du sous-arbre
retiré (`scope` d'un Meter, `serves` d'un système). Pour retirer aussi ces références, utiliser
`park.remove(id, force=True)`.

### 3.6 Building

| Attribut | Type | Unité | Description |
|---|---|---|---|
| `id` | `str` | | Id interne stable (§3.2) |
| `rnb_id` | `str \| None` | | Identifiant RNB, unique dans le parc. Stocké sous forme canonique (majuscules, sans tirets ni espaces), quelle que soit la forme donnée |
| `bdnb_batiment_groupe_id` | `str \| None` | | Groupe BDNB, quand le bâtiment est seul dans son groupe |
| `bdnb_batiment_construction_id` | `str \| None` | | Construction BDNB (c'est elle que référence le RNB) |
| `dpe_ids` | `list[str]` | | Numéros des DPE rattachés |
| `envelope` | `Envelope` | | |
| `heating` | `list[HeatingGenerator]` | | Systèmes propres au bâtiment |
| `cooling` | `list[CoolingGenerator]` | | |
| `dhw` | `list[DHWGenerator]` | | |
| `ventilation` | `list[Ventilation]` | | |
| `pv` | `list[SolarPV]` | | |
| *attributs descriptifs* | | | Voir ci-dessous |

**Attributs descriptifs communs à Site et Building.** La colonne « Hérité » indique si un bâtiment, ou
un sous-site, reprend la valeur de son site parent quand il n'a pas la sienne (§4.4). Les grandeurs
**intensives** sont héritées. Les grandeurs **extensives** (comptages, surfaces) ne le sont jamais, pour
éviter les doubles comptes. Les champs extensifs (`extensive=True`, §3.3) sont `dwelling_count`,
`footprint_area`, `floor_area` et `heated_area`.

| Attribut | Type | Unité | Hérité | Description |
|---|---|---|---|---|
| `name` | sourcé `str` | | non | |
| `address` | sourcé `str` | | oui | |
| `municipality` | sourcé `str` | | oui | Nom de la commune |
| `insee_code` | sourcé `str` | | oui | Code INSEE de la commune |
| `lat`, `lon` | sourcé `float` | ° (WGS84) | oui | Point représentatif |
| `usage` | sourcé `BuildingUsage` | | oui | Usage principal (§3.9) |
| `sub_usages` | sourcé `list[str]` | | oui | Remplace `sub_usage_category_1..3` |
| `construction_year` | sourcé `int` | | oui | |
| `level_count` | sourcé `int` | | oui | Nombre de niveaux, RDC = 1 (fusion de `floor_number` et `nb_level`) |
| `height` | sourcé `float` | m | oui | |
| `dwelling_count` | sourcé `int` | | **non** | Nombre de logements |
| `footprint` | sourcé `dict` (géométrie GeoJSON) | | **non** | Emprise au sol : `Polygon` ou `MultiPolygon`, coordonnées en degrés WGS84 (pas une grandeur : pas d'unité déclarée) |
| `footprint_area` | sourcé `float` | m² | **non** | Surface de l'emprise au sol |
| `floor_area` | sourcé `float` | m² | **non** | Surface de plancher (`floor_surface` du draw.io) ; jamais alimentée par le DPE |
| `heated_area` | sourcé `float` | m² | **non** | Surface chauffée. Le connecteur DPE y range la surface de référence du DPE (habitable ou utile) et la nomme dans `comment` (§8.5) |

**Emprise au sol.** `footprint` est un objet géométrie GeoJSON (RFC 7946) : un dict `{"type": "Polygon",
"coordinates": [...]}` ou `MultiPolygon`, coordonnées `[lon, lat]` en WGS84 (EPSG:4326). Il est stocké
tel quel dans le JSON du parc, sans dépendance géographique. La validation est légère : type, anneaux
fermés d'au moins 4 positions, coordonnées dans les bornes WGS84 ; une géométrie invalide lève
`ValidationError`. Les géométries reçues dans un autre système (Lambert-93 de la BDNB) sont reprojetées
par le connecteur, ce qui demande l'extra `[geo]` (§8.1).

`compute_footprint_area()` (Site et Building) ajoute un candidat `footprint_area` de source `"computed"`,
calculé depuis la valeur effective de `footprint`, sans dépendance : projection équirectangulaire locale
autour du centre du rectangle englobant, mise à l'échelle par les rayons de courbure de l'ellipsoïde WGS84
à cette latitude (une sphère de rayon moyen donnerait jusqu'à 0,2 % d'écart), puis formule du lacet (trous soustraits, polygones d'un `MultiPolygon`
additionnés). L'écart avec une projection à aires égales est inférieur à 0,1 % à l'échelle d'un
bâtiment. Sans emprise, il lève `IncompleteDataError`. Comme pour l'enveloppe (§3.7), c'est un instantané
et la priorité des sources arbitre avec la valeur de la BDNB.

Accès dérivés du bâtiment (non sérialisés) :
- `building.parent` : le site parent direct ou `None` ;
- `building.park` : le parc ;
- `building.serving_systems(kind="heating", include_shared=True)` : systèmes propres, plus systèmes des sites
  ancêtres qui le desservent (`iter_systems()` ne donne que les systèmes propres) ;
- `building.find_meters()` : compteurs dont le `scope` contient ce bâtiment ou un de ses systèmes.

### 3.7 Envelope

L'enveloppe a deux niveaux de description qui coexistent dans le même objet :
- **synthèse** : quelques valeurs globales, toujours disponibles (BDNB, hypothèse, audit) ;
- **composants** (optionnel) : une liste de parois dont la finesse est libre :
  - *par poste* : un composant par type (murs, toiture, plancher bas, vitrages…), ce qui correspond au DPE
    agrégé ;
  - *détaillé* : plusieurs composants par type avec leur orientation, ce qui correspond au XML du DPE ou à un
    audit.

La composition des parois (couches, matériaux) est hors périmètre.

**Envelope : synthèse**

| Attribut | Type | Unité | Hérité | Description |
|---|---|---|---|---|
| `loss_area` | sourcé `float` | m² | **non** | Surface déperditive (`surface` du draw.io) |
| `heat_loss_coefficient` | sourcé `float` | W/K | **non** | Déperditions par transmission (GV du DPE, hors renouvellement d'air) |
| `u_bat` | sourcé `float` | W/(m².K) | oui | Coefficient moyen de déperdition |
| `inertia` | sourcé `Inertia` | | oui | |
| `window_shgc` | sourcé `float` | – | oui | Facteur solaire moyen des vitrages |
| `infiltration_q4pa` | sourcé `float` | m³/(h.m²) | oui | Perméabilité à l'air sous 4 Pa |
| `components` | `list[EnvelopeComponent]` | | **non** | Description détaillée, vide si inconnue |

`loss_area` et `heat_loss_coefficient` sont déclarés `extensive=True` (§3.3). L'enveloppe n'est pas
agrégée en v3, mais la métadonnée est posée dès maintenant pour rester cohérente.

Chaque site ou bâtiment a sa propre enveloppe. `entity.envelope = Envelope(...)` (ou un dict) rattache
la nouvelle enveloppe à l'entité, qui en hérite les valeurs du site parent ; l'ancienne est détachée.
L'enveloppe d'une autre entité ne peut pas être affectée (`HierarchyError`) : on affecte une copie,
`other.envelope.model_copy(deep=True)`. Plus généralement, la copie profonde d'une entité, d'une
enveloppe ou d'un compteur seul est détachée de son propriétaire (pas de parent ni de parc), alors que
celle d'un parc entier garde ses liens internes.

**EnvelopeComponent**

| Attribut | Type | Unité | Description |
|---|---|---|---|
| `id` | `str` | | Généré si absent |
| `kind` | `ComponentKind` | | `WALL`, `ROOF`, `FLOOR`, `WINDOW`, `DOOR`, `THERMAL_BRIDGE` |
| `area` | sourcé `float` | m² | Longueur en m pour un `THERMAL_BRIDGE` |
| `u_value` | sourcé `float` | W/(m².K) | ψ en W/(m.K) pour un `THERMAL_BRIDGE` |
| `heat_loss_coefficient` | sourcé `float` | W/K | Déperdition de la paroi (U·A, ou ψ·L). Seule valeur par poste fournie par l'open data DPE (`deperditions_murs`, etc.) |
| `orientation` | sourcé `Orientation` | | `None` = toutes orientations confondues |
| `adjacency` | sourcé `Adjacency` | | `EXTERIOR`, `UNHEATED_SPACE`, `GROUND`, `ADJACENT_BUILDING` |
| `insulation` | sourcé `InsulationLevel` | | Qualité d'isolation (§3.9) |

**Window(EnvelopeComponent)** (`kind = WINDOW`)

| Attribut | Type | Unité | Description |
|---|---|---|---|
| `shgc` | sourcé `float` | – | Facteur solaire |
| `glazing` | sourcé `GlazingType` | | §3.9 |
| `frame` | sourcé `FrameType` | | §3.9 |

**Cohérence entre les niveaux.** Il n'y a pas de synchronisation automatique. Des calculs explicites
ajoutent un candidat `source="computed"`, et c'est la priorité des sources qui arbitre :

```python
# H d'une paroi = heat_loss_coefficient s'il est renseigné, sinon U·A (ou ψ·L)
env.compute_heat_loss_coefficient()  # Σ H des parois
env.compute_loss_area()              # Σ A des parois hors ponts thermiques
env.compute_u_bat()                  # heat_loss_coefficient / loss_area (valeurs effectives)
env.compute_window_shgc()  # moyenne des shgc pondérée par la surface des vitrages
env.find_components(kind="window", orientation="s")   # filtrage
env.add_component(c), env.remove_component(id)    # gestion des parois (ids uniques dans l'enveloppe)
```

Un calcul impossible (aucune paroi, surface ou U manquant) lève `IncompleteDataError` en citant les parois
concernées.

`"computed"` figure dans la priorité par défaut, après `"audit"` (§4.3). Un candidat calculé est un
instantané : il n'est pas recalculé automatiquement si les parois changent. Il faut relancer le calcul.

### 3.8 Systèmes

Classe de base commune `System` : `id: str`, `name` (sourcé `str`), `serves: list[str] | None`.

- `serves` n'a de sens que pour un système de **site** : c'est la liste des `id` des bâtiments desservis. `None`
  signifie « tous les bâtiments du site et de ses sous-sites ».
- Un système porté par un bâtiment dessert implicitement ce seul bâtiment : son `serves` doit rester `None`
  (sinon `HierarchyError`).
- Les ids de `serves` doivent désigner des bâtiments du site ou de ses sous-sites (sinon `UnknownIdError`).
  C'est vérifié par `add_system` (celui du parc comme celui du site ou du bâtiment) et à chaque affectation
  de `system.serves` sur un système rattaché. Une modification en place (`system.serves.append(...)`) n'est
  pas contrôlée, ni un `serves` reçu à la construction ou au chargement.

**Generator(System)**, base de `HeatingGenerator`, `CoolingGenerator` et `DHWGenerator` (draw.io :
HeatingPlant, CoolingPlant, DomesticHotWater) :

| Attribut | Type | Unité | Description |
|---|---|---|---|
| `technology` | sourcé `GeneratorTechnology` | | Technologie : indique comment interpréter les ratios |
| `energy_type` | sourcé `EnergyType` | | Vecteur énergétique consommé |
| `nominal_power` | sourcé `float` | kW | |
| `nominal_performance_ratio` | sourcé `float` | – | Ratio nominal (fiche technique) : COP, EER ou rendement nominal |
| `seasonal_performance_ratio` | sourcé `float` | – | Ratio saisonnier : SCOP, SEER ou rendement saisonnier (ce que fournit le DPE) |

**Performance ratio** = énergie utile produite / énergie finale consommée. Il **peut dépasser 1** : PAC,
groupe froid, chaudière à condensation sur PCI. Il remplace le `COP` du draw.io et sert pour toutes les
technologies. Les deux ratios sont optionnels et indépendants.

Validation (avertissement `PerformanceRatioWarning`, pas d'erreur), relancée à chaque modification de la
technologie ou d'un ratio : un ratio > 1,1 pour une technologie à combustion, à effet Joule ou une
sous-station, ou un ratio < 1 pour une PAC, un groupe froid ou un chauffe-eau thermodynamique. Les
technologies génériques suivent leur famille : `BOILER` et `WATER_HEATER` comme la combustion,
`HEAT_PUMP` comme une PAC. Les PAC hybrides et le solaire thermique ne sont pas contrôlés.

Chaque classe de système a un `kind` (`SystemKind` : `heating`, `cooling`, `dhw`, `ventilation`, `pv`) qui sert de préfixe
aux ids générés et de filtre dans `building.serving_systems(kind=...)`.

`DHWGenerator` ajoute `storage_volume` (sourcé `float`, L) : volume de stockage, `None` = production
instantanée ou inconnue (open data DPE : `volume_stockage_generateur_n*_ecs_n1`).

**Ventilation(System)**

| Attribut | Type | Unité | Description |
|---|---|---|---|
| `air_change_rate` | sourcé `float` | 1/h | Renouvellements d'air par heure |
| `heat_recovery_efficiency` | sourcé `float` | – | Efficacité de l'échangeur ; `None` = pas d'échangeur |

**SolarPV(System)** (référencé dans le draw.io mais pas défini)

| Attribut | Type | Unité | Description |
|---|---|---|---|
| `peak_power` | sourcé `float` | kWp | |
| `area` | sourcé `float` | m² | |
| `azimuth` | sourcé `float` | ° | 0 = nord, 180 = sud |
| `tilt` | sourcé `float` | ° | 0 = horizontal |

### 3.9 Enums

Les enums sont **compactes et en anglais**. Les connecteurs traduisent les libellés des sources (tables
de correspondance en annexe A), et le libellé d'origine est conservé dans `SourcedValue.raw` (§4.1).
Ce sont des `StrEnum` dont la valeur est le nom en minuscules (`EnergyType.NATURAL_GAS == "natural_gas"`).
Elles sont sérialisées par cette valeur, et les API acceptent indifféremment le membre ou la chaîne, sans
tenir compte de la casse (`Orientation("N") is Orientation.N`).

| Enum | Valeurs |
|---|---|
| `BuildingUsage` | `RESIDENTIAL_INDIVIDUAL`, `RESIDENTIAL_COLLECTIVE`, `TERTIARY`, `SECONDARY`, `PRIMARY`, `OUTBUILDING`, `UNDETERMINED` (les 7 valeurs de `usage_principal_bdnb_open` ; `PRIMARY`, `SECONDARY`, `TERTIARY` sont les secteurs économiques : agriculture, industrie, services) |
| `EnergyType` | `ELECTRICITY`, `NATURAL_GAS`, `FUEL_OIL`, `LPG` (GPL, propane, butane), `WOOD`, `WOOD_LOGS`, `WOOD_PELLETS`, `WOOD_CHIPS`, `COAL`, `DISTRICT_HEATING`, `DISTRICT_COOLING`, `SOLAR`, `OTHER_FOSSIL`, `OTHER` |
| `GeneratorTechnology` | `BOILER` (type inconnu), `BOILER_STANDARD`, `BOILER_LOW_TEMPERATURE`, `BOILER_CONDENSING`, `ELECTRIC_BOILER`, `ELECTRIC_RESISTANCE` (effet Joule), `ELECTRIC_STORAGE_HEATER`, `HEAT_PUMP` (type inconnu), `HEAT_PUMP_AIR_AIR`, `HEAT_PUMP_AIR_WATER`, `HEAT_PUMP_WATER_WATER`, `HEAT_PUMP_GROUND`, `HEAT_PUMP_GAS`, `HEAT_PUMP_HYBRID`, `STOVE_INSERT`, `GAS_RADIATOR`, `WARM_AIR_GENERATOR`, `DISTRICT_SUBSTATION`, `SOLAR_THERMAL`, `WATER_HEATER` (chauffe-eau non thermodynamique, type inconnu), `ELECTRIC_WATER_TANK`, `INSTANT_WATER_HEATER`, `THERMODYNAMIC_WATER_HEATER`, `CHILLER`, `OTHER` |
| `Inertia` | `LIGHT`, `MEDIUM`, `HEAVY`, `VERY_HEAVY` (classes du DPE) |
| `InsulationLevel` | `INSUFFICIENT`, `AVERAGE`, `GOOD`, `VERY_GOOD` (les 4 valeurs du DPE) |
| `GlazingType` | `SINGLE`, `DOUBLE`, `TRIPLE`, `SECONDARY_GLAZING` (survitrage), `GLASS_BRICK`, `POLYCARBONATE` |
| `FrameType` | `WOOD`, `WOOD_METAL`, `PVC`, `METAL_THERMAL_BREAK`, `METAL_NO_THERMAL_BREAK`, `OTHER` |
| `ComponentKind` | `WALL`, `ROOF`, `FLOOR`, `WINDOW`, `DOOR`, `THERMAL_BRIDGE` |
| `Orientation` | `N`, `NE`, `E`, `SE`, `S`, `SW`, `W`, `NW`, `HORIZONTAL` (le DPE n'utilise que N, E, S, O, horizontal) |
| `Adjacency` | `EXTERIOR`, `UNHEATED_SPACE`, `GROUND`, `ADJACENT_BUILDING` |
| `SystemKind` | `HEATING`, `COOLING`, `DHW`, `VENTILATION`, `PV` (nom de la liste correspondante sur Site et Building) |
| `EndUse` (Meters) | `HEATING`, `COOLING`, `DHW`, `VENTILATION`, `LIGHTING`, `APPLIANCES`, `PV_PRODUCTION`, `TOTAL`, `OTHER` |

Dans la BDNB, l'énergie est souvent intégrée au libellé du générateur (« chaudiere gaz condensation »). Le
connecteur découpe ce libellé en `technology` + `energy_type`.

---

## 4. Provenance multi-sources

### 4.1 Principe

Chaque attribut sourcé conserve **une liste de candidats** `SourcedValue` :

```python
class SourcedValue(BaseModel):
    value: Any                 # validé contre le type déclaré du champ
    source: str                # "bdnb", "dpe", "audit", "manual", "bayonne_xlsx_2024"…
    date: datetime.date | None # date de l'information (pas de la saisie)
    comment: str | None
    ref: str | None            # référence dans la source (n° de DPE, id BDNB, nom de fichier, ligne…)
    raw: Any | None            # valeur brute dans la source, avant traduction
                               # ex. "Chaudière gaz à condensation après 2015"
```

La **valeur effective** est choisie parmi les candidats selon la priorité des sources (§4.3).

### 4.2 API

```python
b.construction_year                    # -> 1972 (valeur effective) ou None
b.construction_year = 1968             # équivaut à b.set(..., source="manual")
b.construction_year = None             # retire le candidat "manual" (équivaut à unset(source="manual")) :
                                       # une valeur d'une autre source peut rester ; unset(name) retire tout

b.set("construction_year", 1970, source="bdnb", date=date(2024, 10, 1))
b.set("construction_year", 1972, source="dpe", ref="2364E0123456X")

b.candidates("construction_year")
# [SourcedValue(1972, source="dpe", ref="2364E0123456X"),
#  SourcedValue(1970, source="bdnb", date=2024-10-01)]

b.effective("construction_year")       # -> SourcedValue retenue (avec sa source)
b.unset("construction_year", source="bdnb")   # retire le candidat d'une source
```

Règles :
- Un même `source` ne peut avoir **qu'un candidat par attribut** : un nouveau `set` avec la même source le
  remplace.
- `set` valide la valeur contre le type et les contraintes du champ (Pydantic `TypeAdapter`) et lève
  `ValidationError` si elle est invalide. `set(attr, None, source=s)` équivaut à `unset(attr, source=s)`.
- Un nom d'attribut inconnu lève `UnknownFieldError`.
- À la construction, les kwargs sont enregistrés sous la source `"manual"`, sauf si l'appel précise
  `source=` (une valeur `None` est ignorée) :
  ```python
  park.new_building(rnb_id="...", source="bdnb", construction_year=1970, height=12.5)
  ```

### 4.3 Résolution (SourcePriority)

```python
park.source_priority = SourcePriority(
    default=["manual", "audit", "computed", "dpe", "bdnb", "rnb", "*", "assumption"],
    per_attribute={
        "heated_area": ["bayonne_xlsx_2024", "audit", "dpe", "bdnb"],
    },
)
```

Algorithme pour un attribut :
1. On prend la liste `per_attribute[attr]` si elle existe, sinon `default`.
2. Le candidat retenu est celui dont la source est la mieux classée.
3. Les sources absentes de la liste (ex. `"bayonne_xlsx_2024"`) prennent le rang du joker `"*"`. Sans
   joker, elles passent après toutes les sources listées.
4. En cas d'égalité, la date la plus récente l'emporte, puis le candidat ajouté en dernier.

`SourcePriority` est **immuable** (listes stockées en tuples, `per_attribute` en lecture seule) : on change
la priorité d'un parc en lui affectant une nouvelle instance. `DEFAULT_SOURCE_PRIORITY`, utilisée hors parc,
ne peut donc pas être modifiée par accident. Les noms de sources réservés sont exportés : `MANUAL_SOURCE`
(`"manual"`), `COMPUTED_SOURCE` (`"computed"`) et `WILDCARD_SOURCE` (`"*"`).

La priorité est portée par le **parc** : une entité détachée de tout parc utilise la priorité par défaut
de la lib.

### 4.4 Héritage des valeurs entre sites et bâtiments

Les champs déclarés `inherit=True` (grandeurs intensives, colonne « Hérité » des tableaux §3) se résolvent
en remontant la hiérarchie :

1. L'entité a des candidats propres : la priorité des sources (§4.3) choisit parmi eux.
2. Sinon, on prend la valeur effective du site parent, puis de son parent, etc.
3. **Le niveau le plus proche l'emporte**, quelle que soit la source. Une valeur `bdnb` posée sur le
   bâtiment passe avant une valeur `audit` posée sur le site : une information propre au bâtiment est
   réputée plus précise.

```python
site.set("construction_year", 1972, source="bdnb")   # posé sur le groupe BDNB
b.construction_year                           # -> 1972 (hérité)
b.provider_id("construction_year")                 # -> "bdnb-bg-1111-1HRT-8SVH" (id de l'entité qui fournit la valeur)
b.candidates("construction_year")                # -> [] (candidats propres seulement)
b.inherited_candidates("construction_year")
# [("bdnb-bg-1111-1HRT-8SVH", SourcedValue(1972, source="bdnb"))]
b.set("construction_year", 1985, source="audit")   # valeur propre : masque l'héritage
```

- Les grandeurs extensives (`dwelling_count`, surfaces, `heat_loss_coefficient`…) ne sont **jamais**
  héritées. Une valeur posée sur un site vaut pour le site entier. Les agrégations (somme des bâtiments
  comparée à la valeur du site) sont décrites au §14.3.
- Un champ ajouté par un projet choisit son comportement : `sourced(str, inherit=True)`. Par défaut, il n'est
  pas hérité.
- Un bâtiment ou un site sans parent n'hérite de rien.
- L'enveloppe suit la même règle : l'`Envelope` d'un bâtiment hérite de celle de son site parent (ex. le
  Ubat d'un DPE immeuble posé sur le site).

### 4.5 Implémentation (indicatif)

- Un attribut sourcé est déclaré **sans annotation**, avec son type en premier argument de `sourced` :
  ```python
  class Building(SourcedModel):
      rnb_id: str | None = None                                  # champ Pydantic classique
      heated_area = sourced(float, unit="m²", ge=0, description="Surface chauffée")
      construction_year = sourced(int, inherit=True)
  ```
  `sourced(...)` renvoie un descripteur `SourcedField[T]`. Sur une instance, il renvoie la valeur effective
  (`T | None`), et les IDE et vérificateurs de types connaissent donc son type. Sur la classe, il renvoie
  la définition du champ (`Building.heated_area.unit == "m²"`).
- Les candidats sont stockés dans un vrai champ Pydantic `values: dict[str, list[SourcedValue]]`, qui est
  directement le format JSON du §7.2. Les noms inconnus de la classe y sont conservés tels quels, avec un
  avertissement `UnknownAttributeWarning`.
- Les attributs sourcés ne sont pas des champs Pydantic : `SourcedModel` déclare `SourcedField` dans
  `ignored_types`, surcharge `__setattr__` pour l'affectation directe, et convertit les kwargs du
  constructeur en candidats dans un validateur `mode="before"`.
- Le mécanisme est **hérité** : un champ `sourced(...)` ajouté dans une sous-classe projet est traité
  exactement comme un champ du socle.
- L'héritage des valeurs repose sur deux méthodes internes surchargées par les entités :
  `_inheritance_parent()` renvoie l'objet dont on hérite (le site parent pour un site ou un bâtiment,
  l'enveloppe du site parent pour une enveloppe), et `_entity_id()` renvoie l'id affiché par `provider_id()` (une
  enveloppe renvoie l'id de son propriétaire). L'héritage s'arrête au premier ancêtre qui ne déclare pas
  l'attribut.
- Les sous-classes qui doivent réagir à un changement de candidats (contrôle des ratios d'un générateur)
  surchargent le hook interne `_on_change(name)`, appelé par `set` et `unset`, plutôt que `set`.

---

## 5. Extension par projet

Les attributs propres à un client sont ajoutés par **héritage de `Site` et `Building`** (les seules
entités extensibles). L'extension des systèmes et des Meters n'est pas prévue en v1 ; le mécanisme
`*_cls` pourra être généralisé sans casser l'API si le besoin apparaît. Le code d'extension vit **dans le projet**, hors de bstock.

```python
# projet_bayonne/model.py
from bstock import Building, Site, BuildingPark, sourced

class BayonneSite(Site):
    secteur = sourced(str, description="Secteur de gestion")

class BayonneBuilding(Building):
    code_patrimoine = sourced(str)
    gestionnaire = sourced(str)
    surface_utile_client = sourced(float, unit="m²", ge=0)

def new_park() -> BuildingPark:
    return BuildingPark(name="Ville de Bayonne",
                        site_cls=BayonneSite, building_cls=BayonneBuilding)
```

```python
# projet_bayonne/readers.py : lecteur spécifique au fichier client
def read_patrimoine_xlsx(path, park):
    df = pd.read_excel(path)
    for row in df.itertuples():
        site = park.get(row.code_site) or park.new_site(id=row.code_site, name=row.nom_site,
                                                        source="bayonne_xlsx_2024")
        park.new_building(rnb_id=row.rnb, parent=site, source="bayonne_xlsx_2024",
                          code_patrimoine=row.code, surface_utile_client=row.su)
```

Règles :
- `park.new_site(id, parent=None, ...)` crée un site de premier niveau ou un sous-site.
- `park.new_site(...)` et `park.new_building(...)` instancient `park.site_cls` et `park.building_cls`. Les
  connecteurs RNB, BDNB et DPE (§8) passent aussi par ces méthodes : ils produisent donc des
  `BayonneBuilding` sans rien connaître de Bayonne.
- `park.add_building(b, parent=None)` et `park.add_site(s)` vérifient que l'objet est une instance de la
  classe configurée, ainsi que tous les sites et bâtiments qu'il contient. `site.add_site` et
  `site.add_building` font le même contrôle quand le site appartient à un parc, de même que la construction
  directe `BuildingPark(..., site_cls=..., building_cls=..., sites=[...])`. Dans ce dernier cas, les
  entités données sous forme de dict sont créées avec ces classes. Une instance d'une autre classe lève
  `ProjectClassError`.
- **Chargement** : `BuildingPark.load(path, site_cls=BayonneSite, building_cls=BayonneBuilding)`. Si un
  fichier contient des attributs inconnus de la classe utilisée (par exemple, un parc Bayonne rechargé avec
  `Building`), ils sont **conservés tels quels** et réécrits à la sauvegarde. Un avertissement est émis :
  pas de perte de données.
- Le JSON enregistre le chemin des classes utilisées (`"building_cls": "projet_bayonne.model.BayonneBuilding"`)
  à titre informatif. Un avertissement est émis si la classe passée au chargement diffère. Ces classes ne
  sont jamais importées automatiquement : ouvrir un fichier reçu d'un tiers ne doit pas exécuter de code.

---

## 6. Compteurs et séries temporelles

### 6.1 Meter

| Attribut | Type | Description |
|---|---|---|
| `id` | `str` | Identifiant unique (ex. numéro de PDL) |
| `name` | sourcé `str` | |
| `scope` | `list[str]` | Ids des entités concernées : sites, bâtiments ou systèmes. Peut être vide (compteur pas encore rattaché, ou après `remove(..., force=True)`). Dans un parc, les ids doivent exister : vérifié par `add_meter` et à chaque affectation de `meter.scope` (pas pour une modification en place) |
| `energy_type` | sourcé `EnergyType` | Pour un compteur d'énergie |
| `end_use` | sourcé `EndUse` | Usage mesuré |
| `timestep` | sourcé `str` | Pas de temps nominal (`"10min"`, `"1h"`, `"1D"`…) |
| `channels` | `list[Channel]` | Grandeurs mesurées (une colonne par grandeur) |
| `reader` | `DataReader \| None` | Où lire les données. « Source » est réservé à la provenance des valeurs (§4) : le côté séries temporelles parle de *reader* |
| `original_reader` | `DataReader \| None` | Reader d'origine, conservé après `save(include_data=True)` |
| `parent_meter` | `str \| None` | (v3) Id du compteur dont celui-ci est un **sous-compteur**. `None` = compteur de tête. Sert aux consommations (§6.5) : seuls les compteurs de tête sont additionnés. Optionnel, donc le JSON v2 reste lisible |
| `bloc`, `sub_bloc` | `str \| None` | Surcharge des tags tide ; `None` = valeur dérivée (§6.4). `meter.tide_tags()` renvoie le couple effectif |
| `data` | `pd.DataFrame \| None` | Données en mémoire, **non sérialisées en JSON** |

```python
class Channel(BaseModel):
    name: str        # grandeur : "energy", "power", "index", "temperature"…
    unit: str        # "kWh", "W", "°C"…
    column: str | None = None   # nom de la colonne dans la source brute
```

Exemples :

```python
park.add_meter(Meter(id="PDL_123", scope=["site-ecole-A"], energy_type="electricity",
                     end_use="total", timestep="30min",
                     channels=[Channel(name="power", unit="W", column="valeur")],
                     reader=CsvReader(path="data/enedis_PDL_123.csv", index_col="horodate")))

park.add_meter(Meter(id="SUB_chauff", scope=["heating-chaufferie-A"],
                     energy_type="natural_gas", end_use="heating", timestep="1D",
                     channels=[Channel(name="energy", unit="kWh")]))
```

### 6.2 Lecture des données (readers)

```python
class DataReader(BaseModel):          # base abstraite, discriminée par `type`
    type: str
    tz: str | None                    # localise un index sans fuseau
    tz_ambiguous, tz_nonexistent      # comme pandas.DatetimeIndex.tz_localize
    def read_raw(self) -> pd.DataFrame: ...  # à implémenter par chaque reader
    def read(self) -> pd.DataFrame: ...      # read_raw() + contrôle et localisation de l'index

class CsvReader(DataReader):     type = "csv";     path; index_col; sep; decimal; tz; tz_ambiguous; ...
class ParquetReader(DataReader): type = "parquet"; path
```

- Un CSV dont les horodatages ont des décalages UTC différents (changement d'heure) est converti en UTC.
- Un chemin relatif est résolu par rapport au dossier du parc quand le parc a été chargé depuis un dossier
  (§7).
- Un projet crée son propre reader en sous-classant `DataReader` et en implémentant `read_raw()`, par
  exemple pour une API client. Une sous-classe qui redéfinit `read()` (et contournerait donc les contrôles
  de l'index) lève `InvalidArgumentError` à sa définition.
  L'enregistrement est automatique dès que la classe est importée (registre par `type`, pour le chargement
  JSON).
- Si le JSON référence un reader dont le `type` n'est pas enregistré, la description est conservée telle
  quelle (`UnregisteredReader`), et `read()` lève une `DataReadError` explicite.

### 6.3 Chargement et données en mémoire

```python
m.load_data()            # reader.read() -> sélection et renommage des colonnes -> m.data
m.data                   # DataFrame au format décrit ci-dessous
m.data = df_nettoye      # affectation directe (validée) ; colonnes nommées par channel.name
                         # ou déjà au format tide
```

Contraintes sur `m.data` :
- index `pd.DatetimeIndex` trié, **avec fuseau horaire** (n'importe lequel : UTC, Europe/Paris…). Un index
  sans fuseau est refusé, sauf si la source déclare `tz="Europe/Paris"` (ou autre) : il est alors localisé
  au chargement. Les ambiguïtés des changements d'heure sont gérées par l'option `tz_ambiguous` de la source ;
- une colonne par `Channel`, nommée selon la convention tide (§6.4).

### 6.4 Convention de nommage tide

Les colonnes suivent le format tide `name__unit__bloc__sub_bloc` :

| Tag | Valeur par défaut |
|---|---|
| `name` | `{meter.id}_{channel.name}` (ex. `PDL_123_power`) |
| `unit` | `channel.unit` |
| `bloc` | id de l'entité du `scope` s'il n'y en a qu'une ; sinon `meter.id` |
| `sub_bloc` | `end_use` (ex. `heating`), `other` si inconnu |

`Meter(bloc=..., sub_bloc=...)` surcharge ces valeurs ; `meter.tide_tags()` renvoie le couple effectif et `meter.tide_columns()` les noms de colonnes par canal. Exemple : `PDL_123_power__W__site-ecole-A__total`.

`park.timeseries(...)` assemble un DataFrame unique à partir de plusieurs compteurs, directement utilisable
par tide :

```python
df = park.timeseries(end_use="heating", buildings=[...], load_data=True)  # filtres optionnels
from tide.plumbing import Plumber
Plumber(df).show()
```

- Les index sont convertis dans un fuseau unique (`tz="UTC"` par défaut, paramétrable), puis unis
  (outer join), sans rééchantillonnage : c'est le rôle de tide.
- `load_data=True` appelle `load_data()` sur les compteurs dont `data` est vide.
- Filtres : `end_use`, `energy_type`, `meters` (ids), `buildings` / `sites` (ids ou objets). Un filtre sur
  des bâtiments retient les compteurs dont le `scope` contient ces bâtiments ou leurs systèmes propres.
- Les noms de colonnes commencent par l'id du compteur : ils sont donc uniques dans le parc.
- Un compteur sélectionné sans données en mémoire (et sans `load_data=True`) est ignoré avec un
  `NoDataWarning`.
- `park.meters_for(entité)` et `entité.find_meters()` renvoient les compteurs dont le `scope` contient l'entité
  ou un de ses systèmes propres.

### 6.5 Consommations et indicateurs (v3)

Bilan d'énergie finale par entité, par énergie et par usage sur une période, et indicateur en kWh/m². Les
deux méthodes portent sur une `Selection` (§14.2) et renvoient un DataFrame. Elles ne modifient pas le
modèle.

```python
sel = park.select(within="gs-a")
sel.consumption(start="2024-01-01", end="2025-01-01")                  # une ligne par entité et énergie
sel.consumption(start="2022-01-01", end="2025-01-01", freq="YS")       # une ligne par année
sel.consumption(..., breakdown=True)                                   # ventilation par usage
sel.consumption(..., split="heated_area")                              # répartition au prorata
sel.intensity(start="2024-01-01", end="2025-01-01", area="heated_area")  # kWh/m²
```

**Frontière avec tide.** bstock additionne des énergies, sans traiter de série temporelle :
- un canal `name="energy"` est **sommé** sur la période ;
- un canal `name="index"` donne **dernier relevé moins premier relevé** de la période. Un écart négatif
  (compteur remis à zéro) donne une valeur manquante et un `NoDataWarning` ;
- une puissance (`"power"`) n'est **pas** intégrée. Il faut la convertir en énergie avec tide, puis
  l'affecter par `meter.data = ...` (§6.3). Un compteur sans canal `energy` ni `index` est ignoré avec un
  `NoDataWarning`. S'il a les deux canaux, `energy` est utilisé ;
- unités acceptées : `Wh`, `kWh`, `MWh`, `GWh`, converties en **kWh**. Une autre unité lève
  `TimeSeriesError` ;
- énergie finale seulement : pas de coefficient d'énergie primaire, pas de correction climatique (DJU).

**Période.** `start` (inclus) et `end` (exclu), dates ou horodatages. Un horodatage sans fuseau est lu
dans le fuseau `tz` (`"Europe/Paris"` par défaut, car les périodes calendaires sont une notion métier
locale). Chaque échantillon est rangé selon son horodatage, qui est lu comme le **début** de son pas de
temps. Avec `freq="YS"` (années) ou `freq="MS"` (mois), la fenêtre est découpée en périodes calendaires
dans le fuseau `tz` : c'est une somme par période, pas un rééchantillonnage. La colonne `coverage` donne
la part de la période couverte par des données : nombre d'échantillons non manquants × `timestep` /
durée de la période. Si `timestep` est vide, il est déduit de l'écart médian entre échantillons. Pour un
index, la couverture va du premier au dernier relevé. Il n'y a **jamais d'extrapolation**.

**Compteurs retenus.**
- Seuls les compteurs de tête (`parent_meter=None`) sont additionnés, ce qui évite de compter deux fois
  un compteur général et ses sous-compteurs.
- Chaque id du `scope` est ramené à son **porteur** : un système à son bâtiment ou à son site.
- Sélection de bâtiments : on retient les compteurs dont un porteur est un bâtiment sélectionné, ou un
  site ancêtre d'un bâtiment sélectionné.
- Sélection de sites : on retient les compteurs dont un porteur est dans le sous-arbre d'un site
  sélectionné.
- Un compteur sans données en mémoire est ignoré avec un `NoDataWarning`, sauf avec `load_data=True`
  (comme `timeseries`, §6.4).

**Lignes.**
- Sélection de bâtiments : une ligne au niveau du `scope` de chaque compteur. Il n'y a **pas de
  répartition par défaut**. Un compteur de site apparaît sur la ligne du site. Un compteur qui couvre
  plusieurs entités apparaît sur une ligne dont l'`entity` est la liste des ids joints par `+`. Un
  bâtiment sans compteur n'a pas de ligne.
- Sélection de sites : une ligne par site sélectionné, qui additionne les compteurs de tête de son
  sous-arbre. Les lignes de sites imbriqués se recouvrent et ne s'additionnent donc pas entre elles.

**Ventilation par usage** (`breakdown=True`). Un compteur de tête qui a des sous-compteurs directs est
remplacé par une ligne par `end_use` de ses sous-compteurs. Une ligne `end_use="other"` reçoit le reste :
tête moins somme des sous-compteurs. Un reste négatif émet un `NoDataWarning` et reste affiché tel quel.
Un sous-compteur d'une autre énergie que sa tête lève `TimeSeriesError`. Seul le premier niveau de
sous-compteurs est ventilé.

**Répartition** (`split="<champ extensif>"`). Une ligne de site ou de groupe est répartie sur les
bâtiments qu'elle couvre (sous-arbre du site, ou bâtiments du `scope`) au prorata de la valeur effective
du champ. Seules les parts des bâtiments sélectionnés sont gardées. Ces lignes ont `estimated=True`. Si
un des bâtiments couverts n'a pas de valeur, la ligne n'est pas répartie et un `NoDataWarning` le
signale. Un champ non extensif lève `InvalidArgumentError`.

**Colonnes de `consumption()`** (format long, une ligne par entité × période × énergie × usage) :
`entity`, `period` (début de la période, absent sans `freq`), `energy_type`, `end_use`, `energy`
(kWh), `coverage` (0 à 1), `estimated` (bool), `meters` (liste des ids de compteurs).

**`intensity(area="heated_area", prefer="buildings", **mêmes paramètres)`** ajoute `area` (m²) et
`intensity` (kWh/m²) à `consumption()`. La surface est prise au niveau de la ligne. Pour un bâtiment,
c'est sa valeur effective. Pour un site, c'est la valeur retenue par l'agrégation (§14.3, règle
`prefer`). Pour une ligne qui couvre plusieurs entités, c'est la somme de leurs surfaces. Si une de ces
surfaces manque, `area` et `intensity` sont manquants. `area` doit être un champ extensif
(`InvalidArgumentError` sinon).

**Précisions** (implémentation 0.7.0) :
- `start` et `end` sont obligatoires et passés par mot-clé ; `start >= end` ou un `freq` autre que `"YS"`
  / `"MS"` lève `InvalidArgumentError`.
- Plusieurs compteurs sur une même ligne (même entité, période, énergie, usage et estimation) sont
  additionnés ; la ligne prend la **couverture la plus faible** de ses compteurs, et `meters` les liste
  tous. Une période sans aucun échantillon donne une énergie manquante et une couverture nulle.
- Un compteur sans `end_use` est rangé en `"other"` (comme le `sub_bloc` de tide) ; un compteur sans
  `energy_type` donne une `energy_type` manquante. Un compteur au `scope` vide est ignoré.
- Avec `breakdown=True`, un sous-compteur sans données est ignoré (avertissement) et sa part reste dans
  le reste `"other"`, dont `meters` liste la tête et ses sous-compteurs.
- `split` ne s'applique qu'à une sélection de bâtiments (`InvalidArgumentError` sinon) ; une ligne dont
  la somme du champ vaut zéro n'est pas répartie, comme une ligne à surface manquante.
- Une surface nulle donne une `intensity` manquante.
- Les lignes sont rangées par entité (ordre de première apparition), puis par période.

**Contrôles de `parent_meter`** (implémentés en 0.7.0). L'id doit désigner un autre compteur du parc
(`UnknownIdError` sinon, y compris pour l'id d'un site ou d'un bâtiment). Il ne peut désigner ni le
compteur lui-même, ni un de ses descendants (`HierarchyError`, cycle). Ces contrôles ont lieu à
`add_meter`, à l'affectation de `meter.parent_meter` et au chargement (après lecture de tous les
compteurs, dans n'importe quel ordre). Hors parc, la valeur n'est pas contrôlée, comme `scope`. `park.remove`
d'un compteur qui en a d'autres pour parent lève `EntityInUseError`, sauf avec `force=True`, qui remet
leur `parent_meter` à `None` (ils deviennent compteurs de tête). Dans le JSON, la clé est absente pour un
compteur de tête : un parc sans sous-compteur s'écrit comme en 0.6.0, et le format est resté en
`schema_version` 1. Un parc qui utilise `parent_meter` n'est pas lisible par une version antérieure.

---

## 7. Persistance

### 7.1 API

```python
park.to_json() -> str
BuildingPark.from_json(text, site_cls=..., building_cls=...)   # sous-sites et bâtiments imbriqués
                                                               # créés avec ces classes

park.save("bayonne/")                     # bayonne/park.json
park.save("bayonne/", include_data=True)  # + bayonne/timeseries/<meter_id>.parquet
BuildingPark.load("bayonne/", site_cls=..., building_cls=...)

BuildingPark.json_schema(site_cls=..., building_cls=...) -> dict
park.json_schema()                        # classes du parc par défaut
```

- `save` renvoie le chemin de `park.json` ; `load` accepte ce chemin ou le dossier.

- `load` ne lit pas les séries : `meter.load_data()` ou `park.timeseries(load_data=True)` les chargent à la demande.
- Dans un fichier de parc, les chemins relatifs des readers sont relatifs au dossier du parc.
  À la sauvegarde, les chemins relatifs au répertoire courant sont donc rendus absolus. Un dossier de parc
  sauvegardé avec `include_data=True` peut être déplacé.
- Un fichier écrit avec une autre version du format (`schema_version`) lève `UnsupportedSchemaError`.
  Le format est en version 2 depuis le renommage `data_source` → `reader` ; les fichiers v1 ne
  sont pas migrés.
  Des classes différentes de celles de la sauvegarde émettent un `ClassMismatchWarning`.
- Les attributs sourcés n'étant pas des champs Pydantic, le JSON Schema les décrit à part, dans
  `x-bstock-sourced-attributes` : type, unité, héritage et description par classe.
- Les méthodes Pydantic `model_dump_json` / `model_validate_json` du parc ne sont pas surchargées : elles
  ignorent les métadonnées (version du format, classes). La docstring de `BuildingPark` renvoie vers
  `to_json` / `from_json`.
- `save` modifie le parc : chemins rendus absolus et, avec `include_data=True`, `reader` remplacé
  (voir ci-dessous). C'est délibéré : le parc en mémoire décrit ce qui a été sauvegardé, une nouvelle
  sauvegarde ne relit pas les fichiers d'origine, et les chemins restent valides si le répertoire courant
  change.
- `site_cls` et `building_cls` sont des champs du parc exclus de la sérialisation. Le nom du parc se passe
  par mot-clé : `BuildingPark(name="Ville de Bayonne", site_cls=..., building_cls=...)`.

Avec `include_data=True`, chaque Meter qui a des données en mémoire les écrit dans
`timeseries/<meter_id>.parquet`. Son `reader` devient alors un `ParquetReader` vers ce fichier
(chemin relatif au dossier du parc). L'ancien est conservé dans `meter.original_reader` pour la
traçabilité. Le parc sauvegardé est alors **autonome**.

### 7.2 Format JSON (extrait)

```json
{
  "bstock_version": "0.2.0",
  "schema_version": 2,
  "site_cls": "projet_bayonne.model.BayonneSite",
  "building_cls": "projet_bayonne.model.BayonneBuilding",
  "name": "Ville de Bayonne",
  "source_priority": {"default": ["manual", "audit", "computed", "dpe", "bdnb", "rnb", "*", "assumption"],
                      "per_attribute": {}},
  "sites": [{
    "id": "site-ecole-A",
    "values": {"name": [{"value": "Groupe scolaire A", "source": "bayonne_xlsx_2024"}]},
    "sites": [],
    "heating": [{"id": "heating-chaufferie-A", "serves": null,
                 "values": {"energy_type": [{"value": "natural_gas", "source": "audit"}],
                            "nominal_power": [{"value": 450.0, "source": "audit"}]}}],
    "buildings": [{
      "id": "A1B2C3D4E5F6",
      "rnb_id": "A1B2C3D4E5F6",
      "bdnb_batiment_groupe_id": "bdnb-bg-1111-1HRT-8SVH",
      "dpe_ids": ["2364E0123456X"],
      "values": {
        "construction_year": [
          {"value": 1972, "source": "dpe", "ref": "2364E0123456X"},
          {"value": 1970, "source": "bdnb", "date": "2024-10-01", "ref": "bdnb-bg-1111-1HRT-8SVH"}
        ],
        "code_patrimoine": [{"value": "BAT-0042", "source": "bayonne_xlsx_2024"}]
      },
      "envelope": {"values": {"u_bat": [{"value": 1.2, "source": "assumption",
                                          "comment": "valeur typique 1970-1975"}]}}
    }]
  }],
  "buildings": [],
  "meters": [{
    "id": "PDL_123", "scope": ["site-ecole-A"],
    "channels": [{"name": "power", "unit": "W", "column": "valeur"}],
    "values": {"energy_type": [{"value": "electricity", "source": "manual"}]},
    "reader": {"type": "parquet", "path": "timeseries/PDL_123.parquet"}
  }]
}
```

Seuls les candidats sont sérialisés, pas la valeur effective : elle est recalculée au chargement.

---

## 8. Connecteurs (v2)

Les connecteurs créent et enrichissent les entités du parc à partir des API publiques RNB, BDNB et DPE
(ADEME). Ils ne font qu'appeler l'API publique du modèle : `park.new_building`, `park.new_site`,
`park.move`, `set(..., source=...)`, `add_system`. Ils produisent donc les classes projet du parc
(§5) sans les connaître. Les contrats d'API et les tables de correspondance sont en annexe A.

### 8.1 Organisation

```
src/bstock/connectors/
  __init__.py      # ImportReport ; les sous-modules s'importent explicitement
  _http.py         # session requests, timeout, nouvel essai, pagination
  _report.py       # ImportReport
  _sync.py         # écriture idempotente : candidats, systèmes et parois à id déterministe
  _geo.py          # reprojection des géométries vers WGS84 (extra [geo])
  _mappings.py     # tables de correspondance de l'annexe A (fonctions pures)
  data/            # dpe_generators.csv (annexe A.5)
  rnb.py           # RnbClient, add_buildings, enrich, suggest_matches
  bdnb.py          # BdnbClient, enrich
  dpe.py           # DpeClient, enrich
```

Chaque connecteur a trois couches :
- un **client** (`RnbClient`, `BdnbClient`, `DpeClient`) qui ne fait que récupérer les réponses brutes
  (dicts JSON). URL de base, session `requests` et clé d'API éventuelle sont configurables ;
- des **correspondances** pures, internes : réponse brute → candidats `SourcedValue` (avec `raw` et
  `ref`) ;
- des **fonctions de haut niveau** qui modifient le parc. Elles acceptent un `client=` (sinon un client
  par défaut est créé), ce qui permet de les tester hors réseau avec un faux client.

```python
from bstock.connectors import bdnb, dpe, rnb

report = rnb.add_buildings(park, ["8K3CTZKZ9R27", "A1B2C3D4E5F6"], parent="site-ecole-A")
report = bdnb.enrich(park)                 # tous les bâtiments qui ont un rnb_id
report = dpe.enrich(park, buildings=["A1B2C3D4E5F6"])
```

Dépendances : `requests` (extra `[connectors]`), importé dans les fonctions qui en ont besoin.
shapely, geopandas et pyproj (extra `[geo]`) servent à reprojeter les géométries BDNB et aux exports
géographiques (§9). Un extra manquant lève `MissingDependencyError` avec la commande d'installation.

### 8.2 Règles communes

- **Sources** : `"rnb"`, `"bdnb"` et `"dpe"`, déjà présentes dans la priorité par défaut (§4.3). `date` =
  date de l'information (millésime BDNB, date d'établissement du DPE ; aucune pour le RNB, dont la réponse ne date pas l'information), `ref` =
  identifiant dans la source (`rnb_id`, `batiment_groupe_id` ou `batiment_construction_id`, n° de DPE),
  `raw` = libellé d'origine quand la valeur a été traduite.
- **Idempotence** : relancer un connecteur remplace les candidats de sa source (un candidat par source,
  §4.2). Les systèmes et les parois qu'il crée ont un id déterministe, `<kind>-<owner_id>-<source>-<n>`
  pour un système (ex. `heating-bdnb-bg-1111-1HRT-8SVH-bdnb-1`) et `<source>-<kind>` pour une paroi par
  poste (ex. `dpe-wall`) : ils sont mis à jour au lieu d'être dupliqués. Ceux qu'une nouvelle réponse ne
  produit plus sont retirés, sauf s'ils sont référencés (`scope` d'un Meter) : ils sont alors conservés
  et signalés dans le rapport.
- **Libellé inconnu** des tables de correspondance : la valeur prend `OTHER` quand l'enum l'a, sinon elle
  n'est pas posée ; un `UnmappedValueWarning` cite le libellé, et `raw` le conserve.
- **Rapport** : les fonctions de haut niveau renvoient un `ImportReport` (ids créés, enrichis, non
  trouvés, ambigus, et un message par id), avec `report.to_dataframe()`. Les cas attendus (bâtiment
  absent de la source, DPE d'appartement ignoré) y figurent au lieu d'émettre des avertissements. Les ids
  sont ceux des entités du parc ; un id externe sans entité correspondante (RNB id inconnu passé à
  `add_buildings`) figure sous cet id externe. `report.add()` et `report.note()` restent publics pour les
  connecteurs écrits par un projet.
- **Erreurs** : une erreur HTTP persistante ou une réponse inattendue lève `ConnectorError`. Les erreurs
  429 et 5xx sont réessayées avec attente avant d'abandonner.
- **Pas de cache** dans bstock : on sauvegarde le parc enrichi, ou on passe au client sa propre session
  (`requests-cache`, par exemple).
- Le format JSON du parc ne change pas avec les connecteurs.

### 8.3 RNB

- `rnb.add_buildings(park, rnb_ids, parent=None, *, client=None) -> ImportReport` : crée via
  `park.new_building` les bâtiments absents du parc et enrichit ceux qui existent déjà (recherche par
  `get_by_rnb`).
- `rnb.enrich(park, buildings=None, *, client=None) -> ImportReport` : bâtiments du parc qui ont un
  `rnb_id` (tous si `buildings` est `None`).
- Une requête par bâtiment : l'API n'offre pas de lecture groupée par liste d'ids.
- Correspondances : point → `lat` / `lon`, forme → `footprint` (si c'est un `Polygon` ou un
  `MultiPolygon`), première adresse → `address`, `municipality`, `insee_code`, identifiant externe BDNB →
  `bdnb_batiment_construction_id` s'il n'est pas déjà renseigné (ces liens datent de 2023 : la BDNB, qui
  référence elle-même le `rnb_id`, fait foi).
- Un `rnb_id` est normalisé (majuscules, sans tirets) avant comparaison, comme dans le modèle (§3.6).
- Signalés dans le rapport, sans rien modifier ni supprimer dans le parc : id inconnu du RNB, bâtiment
  démoli (`status`), id désactivé après une fusion ou une scission (`is_active: false` ; le rapport
  donne le ou les ids successeurs lus dans l'historique du bâtiment).

**Bâtiments sans `rnb_id`.** `rnb.suggest_matches(park, buildings=None, *, by="point", radius=...,
assign=None, client=None) -> pd.DataFrame` propose des bâtiments RNB candidats, cherchés autour du point
(`lat` / `lon`, rayon ≤ 1000 m) ou à partir de l'adresse (`by="address"`). Le DataFrame a une ligne par
couple (bâtiment, candidat) : id du bâtiment, `rnb_id` candidat, distance (m, 0 si le point est dans
l'emprise) ou score de géocodage de l'adresse, adresse RNB. L'utilisateur valide puis pose
`building.rnb_id`. Aucune affectation automatique, sauf `assign="unique"` : le `rnb_id` est posé quand il
n'y a qu'un candidat (dans le rayon, ou à l'adresse). Un `rnb_id` déjà utilisé dans le parc est signalé
mais jamais affecté. Une valeur invalide de `by`, `radius` ou `assign` lève `InvalidArgumentError`.
Contrairement aux autres fonctions des connecteurs, le résultat est un tableau de propositions à examiner,
pas un `ImportReport`. `assign` est une chaîne et non un booléen pour accueillir d'autres stratégies.

Précisions d'implémentation :
- les candidats RNB n'ont pas de `date` (la réponse ne date pas l'information) ; `ref` = `rnb_id`, et le
  `comment` de `address` donne la clé BAN (`BAN 64102_0885_00004`). L'adresse est mise sur une ligne :
  `4 Rue de la Gabarre, 64100 Bayonne` ;
- un relancement remplace les candidats `"rnb"` et retire ceux que le RNB ne donne plus ;
- id inconnu → `not_found` ; bâtiment démoli ou id inactif → un message seulement, sans statut ; dans
  `enrich`, un bâtiment sans `rnb_id` est signalé (renvoi vers `suggest_matches`) ;
- `suggest_matches` : rayon par défaut 30 m ; le point et l'adresse sont les valeurs effectives, donc
  hérités du site si le bâtiment n'en a pas. Colonnes : `building_id`, `rnb_id`, `distance`, `score`,
  `address`, `used_by` (bâtiment du parc qui porte déjà ce `rnb_id`), `assigned`. Un bâtiment sans point
  (ou sans adresse) ou sans candidat n'a pas de ligne.

La recherche par adresse s'appuie sur l'API RNB, qui géocode elle-même l'adresse avec la BAN et ne garde
que la meilleure adresse : il n'y a pas de géocodage séparé dans bstock. Une adresse sans numéro ou mal
reconnue ne donne aucun candidat.

### 8.4 BDNB

`bdnb.enrich(park, buildings=None, *, group_sites=True, client=None) -> ImportReport` part du `rnb_id` des
bâtiments.

- **Quotas** : sans clé, l'API sert 10 lignes par appel et 10 000 appels par mois. Le client regroupe
  donc les requêtes par lots d'ids (`in.(...)`) et accepte une clé d'API (`BdnbClient(api_key=...)`),
  qui relève ces limites. Il faut compter quatre appels pour dix bâtiments, plus un appel par groupe pour
  compter ses constructions.
- Valeurs de la **construction** (`batiment_construction`) → bâtiment : `bdnb_batiment_construction_id`,
  `height` (`hauteur`), `footprint_area` (`s_geom_cstr`), `footprint` (`geom_cstr`). Les géométries
  BDNB sont en Lambert-93 : elles sont reprojetées en WGS84 avec l'extra `[geo]`. Sans cet extra, elles
  sont ignorées avec un avertissement, et le reste de l'enrichissement se fait normalement.
- Valeurs du **groupe** (`batiment_groupe_complet`) : `usage`, `construction_year`, `dwelling_count`,
  `level_count`, `height` (hauteur moyenne), `footprint_area` et `footprint` du groupe, générateurs de
  chauffage, d'ECS et de froid (principal et appoint, seuls exposés par l'offre ouverte). Les énergies
  d'ECS et de froid viennent de `batiment_groupe_dpe_representatif_logement`. Le numéro du DPE
  représentatif est ajouté aux `dpe_ids` de l'entité qui reçoit les valeurs du groupe.
- **Provenance** : `ref` = `batiment_construction_id` ou `batiment_groupe_id`. L'API ne date pas ses
  réponses : `date` est vide, sauf pour les générateurs, qui reçoivent la date du DPE représentatif dont
  ils sont issus.
- Plusieurs constructions BDNB pour un même `rnb_id` (cas non observé, mais possible) : bâtiment signalé
  comme ambigu dans le rapport et non enrichi.
- **Règle des groupes** : on compte les constructions du groupe **dans la BDNB**, pas dans le parc.
  - Groupe d'une seule construction : les valeurs du groupe sont posées sur le bâtiment, qui reçoit
    `bdnb_batiment_groupe_id`.
  - Groupe de plusieurs constructions : un sous-site `id = batiment_groupe_id`, avec
    `bdnb_batiment_groupe_id`, est créé sous le parent commun des bâtiments du groupe (ou réutilisé
    s'il existe), et ces bâtiments y sont déplacés (`park.move`). Les valeurs du groupe sont posées sur
    le sous-site : les grandeurs intensives sont héritées par les bâtiments, les extensives restent au
    niveau du groupe (§4.4). C'est le cas même si un seul bâtiment du groupe est dans le parc, car les
    valeurs extensives du groupe ne valent pas pour ce seul bâtiment.
  - Bâtiments d'un même groupe rangés sous des parents différents : pas de restructuration ; les valeurs
    intensives du groupe sont posées sur chaque bâtiment, les extensives ne le sont pas, et le rapport
    le signale.
  - `group_sites=False` désactive la création des sous-sites : on applique alors la règle précédente.

Précisions d'implémentation :
- Groupe d'une construction : la construction et le groupe donnent tous deux `height`,
  `footprint_area` et `footprint`, et une source n'a qu'un candidat par attribut ; la valeur de la
  construction l'emporte (plus précise que la moyenne du groupe).
- Générateurs : un système par générateur renseigné, principal puis appoint, avec `name` = `"main"` ou
  `"backup"`, et les ids déterministes du §8.2 (`heating-<entité>-bdnb-1`…). Les dates de la BDNB sont
  des horodatages UTC sans décalage (`2026-03-09T23:00:00` = minuit à Paris le 10 mars) : elles sont
  converties en date de Paris.
- Bâtiments du groupe répartis (parents différents ou `group_sites=False`) : chacun reçoit les valeurs
  intensives, les générateurs et le numéro du DPE représentatif du groupe, et le rapport le signale.
- Sous-site de groupe : un site existant de même id est réutilisé là où il se trouve. Un bâtiment que
  `park.move` refuse de déplacer (servi par un système d'un site qu'il quitterait) reste en place, reçoit
  les valeurs intensives, et le rapport le signale.
- Taille des lots : 10 ids par appel sans clé, 1 000 avec une clé (offre Open+), réglable par
  `BdnbClient(page_size=...)`. Un lot plein est complété par `offset`.

### 8.5 DPE

`dpe.enrich(park, buildings=None, *, client=None) -> ImportReport` cherche les DPE par `rnb_id` (colonne
`id_rnb`, renseignée sur environ la moitié des DPE) et par les `dpe_ids` déjà connus (posés par
l'utilisateur ou par le connecteur BDNB), dans trois jeux de l'ADEME : logements existants (depuis
juillet 2021), logements neufs et tertiaire. Le XML des DPE n'est pas couvert.

- **Échelle** : seuls les DPE qui portent sur un bâtiment entier alimentent les attributs :
  `type_batiment` `immeuble` ou `maison` (logement), `methode_dpe` sans « dans un bâtiment de logement »
  (tertiaire). Les DPE d'appartement et les DPE tertiaires d'un local situé dans un immeuble de
  logements sont seulement ajoutés à `dpe_ids`. Un DPE d'appartement généré à partir d'un DPE immeuble
  (`numero_dpe_immeuble_associe`) mène à ce DPE immeuble, qui est alors utilisé.
- **Plusieurs DPE** : un DPE remplacé par un autre (`numero_dpe_remplace`) est écarté ; parmi les
  autres, le plus récent l'emporte (un seul candidat `"dpe"` par attribut) ; `date` =
  `date_etablissement_dpe`, `ref` = numéro du DPE. Tous les numéros trouvés sont ajoutés à `dpe_ids`.
- Les valeurs vont sur l'entité qui porte le DPE : le bâtiment trouvé par `rnb_id` ou `dpe_ids`, ou le
  site dont les `dpe_ids` contiennent le numéro (DPE immeuble posé sur un groupe).
- **Description** : `construction_year` (`annee_construction` ; la période seule ne donne pas de
  valeur), `dwelling_count` (`nombre_appartement` pour un immeuble, 1 pour une maison), `level_count`
  (`nombre_niveau_immeuble`), `usage` (maison → `RESIDENTIAL_INDIVIDUAL`, immeuble →
  `RESIDENTIAL_COLLECTIVE`, tertiaire → `TERTIARY`).
- **Surfaces** : la surface de référence du DPE (`surface_habitable_immeuble` ou `_logement` pour un
  logement, `surface_utile` pour le tertiaire) va dans `heated_area`, et `comment` la nomme.
  `floor_area` n'est jamais alimentée par le DPE.
- **Enveloppe** (DPE logement) : `u_bat`, `inertia`, et une paroi par poste (`dpe-wall`, `dpe-roof`,
  `dpe-floor`, `dpe-window`, `dpe-door`, `dpe-thermal_bridge`) avec `heat_loss_coefficient` (W/K) et la
  qualité d'isolation. Les fenêtres et les portes reçoivent la qualité des menuiseries ; la toiture, la
  moins bonne des trois qualités de plancher haut renseignées (comble perdu, comble aménagé, toit
  terrasse). Le DPE tertiaire ne décrit pas l'enveloppe. L'open data ne donne pas le Q4Pa.
- **Systèmes** (DPE logement) : jusqu'à quatre générateurs de chauffage (deux installations de deux
  générateurs) et deux d'ECS, avec `technology`, `energy_type` et, pour l'ECS, `storage_volume` (L) et
  le COP en `nominal_performance_ratio`. L'open data ne donne aucun rendement de chauffage. Ids
  déterministes (§8.2).
- **Froid** (DPE logement) : un générateur (`type_generateur_froid`, énergie `type_energie_climatisation`
  quand le jeu la donne), traduit par la table A.5.
- **Tertiaire** : un générateur de chauffage avec la seule énergie principale
  (`type_energie_principale_chauffage`), sans technologie.

Précisions d'implémentation :
- `buildings=None` vise les bâtiments qui ont un `rnb_id` ou des `dpe_ids`, et les sites qui ont des
  `dpe_ids`. Une liste explicite ne vise que ces bâtiments.
- Les DPE sont cherchés par numéro dans le jeu que désigne le 5e caractère du numéro, et par `id_rnb`
  dans les trois jeux, en ne demandant que les colonnes utiles (`select`, `count=false`). Le jeu
  `dpe02neuf` n'a pas les colonnes de qualité des combles perdus et du toit terrasse, ni
  `type_energie_climatisation`.
- Tous les numéros trouvés, sauf ceux d'un DPE remplacé, sont ajoutés à `dpe_ids` : un immeuble peut
  ainsi recevoir les dizaines de DPE de ses appartements.
- Un nouveau DPE retenu remplace l'ensemble des candidats `"dpe"`, parois et systèmes compris (un DPE
  tertiaire retire donc les parois d'un DPE logement plus ancien).
- Générateurs : `name` = `installation <i>, generator <g>` (chauffage) ou `generator <g>` (ECS) ; un
  volume de stockage nul (chaudière mixte) n'est pas posé.

---

## 9. Exports (v2)

```python
park.to_dataframe(kind="building", provenance=False) -> pd.DataFrame        # ou kind="site"
park.to_geodataframe(kind="building", geometry="footprint")                 # extra [geo]
park.to_geopackage("parc.gpkg", geometry="footprint")                       # couches sites et buildings
park.to_geoparquet("batiments.parquet", kind="building", geometry="footprint")
```

- Une ligne par entité, indexée par `id`. Colonnes : `rnb_id`, identifiants BDNB, `dpe_ids`,
  `parent_id`, puis la **valeur effective** de chaque attribut sourcé, héritage compris, y compris les
  attributs ajoutés par une sous-classe projet. Les enums sont rendues par leur valeur (chaîne), les
  listes restent des listes, les attributs entiers sont en `Int64` (nullable) et une valeur absente est
  manquante au sens de pandas (`pd.isna`). `footprint` est exclu de `to_dataframe`. Un autre `kind`
  lève `UnknownKindError` (sous-classe d'`InvalidArgumentError`, elle-même sous-classe de `ValueError`).
- `provenance=True` ajoute pour chaque attribut `<attr>_source` (source de la valeur retenue) et
  `<attr>_provider` (id de l'entité qui la fournit, différent de la ligne si elle est héritée).
- `to_geodataframe` utilise `footprint` (`geometry="footprint"`) ou le point `lat` / `lon` effectif, donc
  hérité du site au besoin (`geometry="point"`), en EPSG:4326. Une entité sans géométrie est conservée
  avec une géométrie manquante (`None`). Une autre valeur de `geometry` lève `InvalidArgumentError`.
- GeoPackage et GeoParquet sont écrits avec geopandas (pyogrio). Le GeoPackage a deux couches, `sites`
  et `buildings` (vides s'il n'y a pas d'entité), avec `id` en colonne ; il n'accepte pas les listes ni
  les dicts, qui sont écrits en chaîne JSON, et ses couches sont remplacées si le fichier existe. Le
  GeoParquet garde les listes et l'index `id`.
- Sans l'extra `[geo]`, ces trois exports lèvent `MissingDependencyError`.

---

## 10. Exemple de bout en bout (v1)

Version exécutable, avec des données fictives : `examples/bayonne.py` (vérifiée par les tests).

```python
from datetime import date
from bstock import BuildingPark, HeatingGenerator, Meter, Channel, CsvReader
from projet_bayonne.model import new_park, BayonneSite, BayonneBuilding
from projet_bayonne.readers import read_patrimoine_xlsx

park = new_park()
read_patrimoine_xlsx("clients/bayonne/patrimoine_2024.xlsx", park)

b = park.get("A1B2C3D4E5F6")
b.set("heated_area", 1250.0, source="audit", date=date(2025, 3, 12), comment="relevé sur plans")
b.envelope.u_bat = 0.9                         # source "manual"

park.add_system(HeatingGenerator(id="heating-chaufferie-A", technology="boiler_condensing",
                                  energy_type="natural_gas", nominal_power=450.0,
                                  seasonal_performance_ratio=0.95, source="audit"),
                 owner=b.parent)

park.add_meter(Meter(id="PDL_123", scope=[b.parent.id], end_use="total", timestep="30min",
                     energy_type="electricity",
                     channels=[Channel(name="power", unit="W", column="valeur")],
                     reader=CsvReader(path="data/enedis_PDL_123.csv", index_col="horodate")))

df = park.timeseries(load_data=True)
park.save("out/bayonne/", include_data=True)

# plus tard, ailleurs
park2 = BuildingPark.load("out/bayonne/", site_cls=BayonneSite, building_cls=BayonneBuilding)
park2.get("A1B2C3D4E5F6").candidates("heated_area")
```

---

## 11. Organisation du package

```
src/bstock/
  __init__.py          # API publique
  provenance.py        # SourcedValue, SourcedModel, sourced(), SourcePriority
  enums.py             # toutes les enums (§3.9)
  park.py              # BuildingPark
  description.py       # attributs descriptifs communs à Site et Building
  site.py              # Site
  building.py          # Building
  envelope.py          # Envelope, EnvelopeComponent, Window
  systems.py           # System, Generator, Heating/Cooling/DHWGenerator, Ventilation, SolarPV
  meters.py            # Meter, Channel, conventions tide
  readers.py           # DataReader, CsvReader, ParquetReader, registre
  io.py                # to_json/from_json, save/load, JSON Schema
  export.py            # to_dataframe, to_geodataframe, GeoPackage, GeoParquet (§9)
  display.py           # v3 : repr, tree() / TreeView, describe(), HTML (§14.1)
  selection.py         # v3 : Selection, critères (§14.2)
  analysis.py          # v3 : agrégations, complétude, provenance (§14.3, §14.4)
  consumption.py       # v3 : consommations et kWh/m² (§6.5)
  plot.py              # v3 : accesseur Selection.plot, extra [plot] (§14.5)
  _geometry.py         # validation GeoJSON, aire de l'emprise (sans dépendance)
  _deps.py             # import des dépendances optionnelles (MissingDependencyError)
  connectors/          # RNB, BDNB, DPE (§8.1)
  exceptions.py        # BstockError, BstockWarning et leurs sous-classes
  _ids.py              # génération et contrôle des identifiants
tests/                 # un fichier par module, plus test_examples.py
tests/data/            # réponses d'API enregistrées (rnb/, bdnb/, dpe/) pour les tests hors réseau
examples/              # exemples de bout en bout (bayonne, connectors, plots), chacun en version script
                       # (<nom>.py) et en version interactive par cellules (<nom>_interactive.py)
scripts/               # outils de maintenance (régénération de dpe_generators.csv)
```

- **API publique** : `bstock.__all__`, plus les `__all__` des sous-modules documentés `bstock.io`,
  `bstock.export`, `bstock.connectors`, `bstock.connectors.rnb`, `bstock.connectors.bdnb` et
  `bstock.connectors.dpe`. Les `__all__` des autres modules ne listent que des noms réexportés par
  `bstock` ; leurs autres noms, et les modules préfixés par `_`, sont internes.
- Python ≥ 3.11.
- Dépendances : `pydantic>=2`, `pandas`, `pyarrow` (Parquet). Extras : `[plot]` (plotly, seule lib de
  graphiques, ≥ 5.24), `[connectors]` (requests), `[geo]` (shapely, geopandas, pyproj), `[dev]` (pytest, ruff). tide n'est **pas** une dépendance : bstock
  respecte seulement sa convention de nommage.
- Outillage et conventions : voir `CLAUDE.md`. Plan de réalisation : `PLAN.md`.

---

## 12. Feuille de route

| Version | Contenu |
|---|---|
| **v1** | Modèle (Park, Site, Building, Envelope, systèmes, Meter), provenance multi-sources, extension par héritage, sources CSV/Parquet, `timeseries()` au format tide, save/load JSON + Parquet, JSON Schema |
| **v2** | Connecteurs API RNB, BDNB, DPE (§8) avec suggestions d'appariement RNB ; exports `to_dataframe()`, `to_geodataframe()`, GeoPackage, GeoParquet (§9) ; emprise au sol `footprint` et `compute_footprint_area()` (§3.6) |
| v3.a (0.4.0) | Lisibilité : repr, `tree()`, `describe()`, HTML Jupyter (§14.1) ; retrait des noms dépréciés ci-dessous |
| v3.b (0.5.0) | Sélection : `park.select(...)`, `Selection` (§14.2) |
| v3.c (0.6.0) | Analyse : métadonnée `extensive` (§3.3), agrégations (§14.3), complétude et provenance (§14.4) |
| v3.d (0.7.0) | Consommations mesurées et kWh/m², `Meter.parent_meter` (§6.5) |
| v3.e (0.8.0) | Graphiques plotly : `sel.plot.*` (§14.5) |

Noms dépréciés en 0.2.0, **retirés en 0.4.0** (v3.a) :

| Ancien nom | Remplacé par |
|---|---|
| `entity.candidates(name, inherited=True)` | `entity.inherited_candidates(name)` |
| `entity.meters()` | `entity.find_meters()` |
| `building.systems(...)` | `building.serving_systems(...)` |
| `meter.column_names()` | `meter.tide_columns()` |

---

## 13. Questions ouvertes

1. Bâtiment client qui couvre **plusieurs ID RNB** : il se modélise comme un site contenant ces bâtiments.
   À valider à l'usage.
2. Valeurs proposées pour la v3, à confirmer pendant l'implémentation : troncature du repr à 30 lignes
   (§14.1), tolérance relative des conflits `rtol=0.05` (§14.4), fuseau des périodes de consommation
   `tz="Europe/Paris"` (§6.5).
3. `aggregate(by=...)` ignore les valeurs propres des sites (§14.3). Si le besoin apparaît, une option
   pourrait les rattacher à un groupe.

---

## 14. Exploration et analyse (v3)

Décisions issues de l'interview du 2026-10-15 (tableau E1 à G2 de `PLAN.md`). Les consommations
mesurées sont au §6.5.

### 14.1 Lisibilité (v3.a)

**Règle des repr.** Le repr dépend du rôle de l'objet.
- **Conteneurs** (`BuildingPark`, `Site`) : on les tape seuls dans une cellule pour avoir une vue
  d'ensemble. Leur repr est un **arbre résumé**, précédé d'une ligne de comptage et tronqué au-delà de
  30 lignes, avec une dernière ligne qui indique le nombre de lignes masquées. C'est `str(x.tree())`
  avec les paramètres par défaut.
- **Entités feuilles** (`Building`, systèmes, `Meter`, `Envelope`, parois) : on les voit dans des listes
  et des messages. Leur repr tient sur **une ligne** : nom de la classe (celle du projet le cas échéant),
  `id`, puis quelques valeurs clés non vides. Pour un bâtiment : `rnb_id` s'il diffère de l'`id`, `name`,
  `parent`. Pour un générateur : `technology`, `energy_type`, `nominal_power` avec son unité. Pour un
  compteur : `energy_type`, `end_use`, `scope`, et si des données sont en mémoire.
  Exemple : `BayonneBuilding(id='A1B2C3D4E5F6', name='École Jean Jaurès', parent='gs-a')`.

```
BuildingPark 'Ville de Bayonne': 3 sites, 12 buildings, 4 meters
├── [site] gs-a  Groupe scolaire A  (3 buildings)
│   ├── [site] bdnb-bg-1111-1HRT-8SVH  (2 buildings)
│   │   ├── [building] A1B2C3D4E5F6  École Jean Jaurès
│   │   └── [building] B2C3D4E5F6G7
│   └── [building] C3D4E5F6G7H8  Gymnase
└── [building] bldg-1f3a  Mairie annexe
… 18 more lines: call .tree() for the full tree
```

**`tree(*, systems=False, meters=False, max_depth=None, fields=None)`** est défini sur `BuildingPark`,
`Site` et `Selection` (§14.2). Il renvoie un objet `TreeView`. Son `str()` et son repr donnent le texte
complet, sans troncature. Son `_repr_html_` donne la version HTML. `lines()` renvoie les
lignes du texte et `truncated(max_lines)` le texte coupé, comme dans le repr.
- Par défaut, l'arbre montre les sites et les bâtiments, dans l'ordre de `iter_sites` / `iter_buildings`.
- Chaque nœud affiche son type, son `id` et son `name`. Un site affiche aussi le nombre de bâtiments de
  son sous-arbre.
- `fields=["usage", "heated_area"]` ajoute les **valeurs effectives** de ces champs (héritage compris),
  avec leur unité. Un champ inconnu lève `UnknownFieldError`.
- `systems=True` ajoute les systèmes propres sous leur porteur (`[heating] id  name`). `meters=True`
  ajoute les compteurs sous chaque porteur de leur `scope` (un compteur à plusieurs porteurs apparaît
  plusieurs fois).
- `max_depth=n` coupe l'arbre au niveau `n` (1 = premier niveau). Un nœud coupé indique ce qu'il
  contient (`… 5 buildings`).

**`describe()`** est défini sur toute entité sourcée (Site, Building, Envelope, systèmes, Meter). Il
renvoie un DataFrame avec une ligne par attribut sourcé, dans l'ordre de déclaration, y compris ceux de la
sous-classe projet et ceux qui n'ont pas de valeur. L'index est le nom de l'attribut. Colonnes : `value`
(valeur effective, enums rendus par leur valeur), `unit`, `source` (source retenue), `provider` (id de
l'entité qui fournit la valeur), `inherited` (bool) et `candidates` (nombre de candidats propres).

**HTML (Jupyter).** `BuildingPark`, `Site`, `Selection` et `TreeView` ont un `_repr_html_`. C'est un arbre
repliable en `<details>` imbriqués, sans JavaScript, avec un style intégré (police à chasse fixe, bordure
semi-transparente lisible en thème clair et sombre). La ligne d'en-tête et le premier niveau sont visibles ;
chaque nœud qui a un contenu est replié et s'ouvre au clic, ce qui garde l'affichage court sur un gros
parc. Les libellés sont échappés. L'arbre HTML suit les options de `tree()` et n'est pas tronqué, puisqu'il
est repliable. Les entités feuilles n'ont pas de HTML propre : Jupyter affiche leur repr texte.

### 14.2 Sélection (v3.b)

```python
sel = park.select(usage="tertiary", construction_year__lt=1975, within="gs-a")
sel = sel.filter(heated_area__isnull=False)
sel = park.select(lambda b: b.height is not None and b.height > 2 * b.level_count, kind="building")
tertiaire_sans_rnb = park.select(usage="tertiary") - park.select(has_rnb_id=True)
len(sel); list(sel); "A1B2C3D4E5F6" in sel; sel.ids; sel.kind
sel.to_dataframe(provenance=True); sel.tree(fields=["usage"])
```

`park.select(predicate=None, *, kind="building", **criteria)` renvoie une `Selection`. Sans critère, la
sélection contient toutes les entités du `kind` : `park.select()` est le point d'entrée des analyses sur
tout le parc. `kind` vaut `"building"` ou `"site"` : une sélection ne mélange jamais les deux. Un autre
`kind` lève `UnknownKindError`. Les systèmes et les compteurs ne se sélectionnent pas : leurs filtres
existent déjà (`iter_systems(kind)`, `timeseries(...)`).

**`Selection`** :
- C'est un **instantané** : elle retient les ids et le parc au moment de sa création. Elle se parcourt
  dans l'ordre de `park.iter_buildings()` / `iter_sites()`, qui est aussi celui de `to_dataframe()`
  (les bâtiments directs d'un site avant ceux de ses sous-sites), et rend les entités du parc. Si une entité a été retirée du parc depuis,
  l'utilisation de la sélection lève `UnknownIdError`. Pour tenir compte des changements du parc, on
  refait la sélection.
- `sel.filter(predicate=None, **criteria)` renvoie une nouvelle sélection, plus restreinte, du même
  `kind`.
- Opérations d'ensemble `sel_a | sel_b`, `sel_a & sel_b` et `sel_a - sel_b`. Les deux sélections doivent
  avoir le même parc et le même `kind` (`InvalidArgumentError` sinon). Le résultat suit le même ordre.
- Propriétés `park`, `kind` et `ids` (tuple) ; `len(sel)`, `list(sel)`, `"id" in sel` ou `entité in sel`.
- Méthodes : `to_dataframe(provenance=False)` (§9, restreint aux entités sélectionnées), `tree(...)`
  (§14.1 : entités sélectionnées et leurs sites ancêtres ; un site compte les bâtiments sélectionnés de
  son sous-arbre, ou tous ses bâtiments dans une sélection de sites, qui ne montre pas les bâtiments ;
  l'en-tête est le repr de la sélection suivi du parc), `aggregate`, `completeness`, `provenance`,
  `conflicts` (§14.3, §14.4), `consumption`, `intensity` (§6.5) et l'accesseur `plot` (§14.5).
- Repr sur une ligne : `Selection(kind='building', 42 entities)`. En HTML, c'est l'arbre de la sélection.

**Critères.** Tous les critères se combinent par ET, et le prédicat aussi. Ils portent sur la **valeur
effective**, héritage compris : un bâtiment qui hérite `usage="tertiary"` de son site est retenu.

| Forme | Sens |
|---|---|
| `attr=v` | Valeur effective égale à `v`. Une enum accepte sa chaîne |
| `attr__in=[...]` | Valeur dans la liste |
| `attr__lt`, `__le`, `__gt`, `__ge` | Comparaison |
| `attr__isnull=True / False` | Valeur absente / présente |
| `attr__contains=v` | Attribut liste (`sub_usages`, `dpe_ids`) qui contient `v` |
| `within=site` | Dans le sous-arbre du site (objet ou id), à toute profondeur. Pour `kind="site"`, il s'agit des descendants stricts |
| `parent=site` | Parent direct égal à `site` ; `parent=None` désigne le premier niveau |
| `has_rnb_id=True / False` | Bâtiments seulement |
| `has_meter=True / False` | `find_meters()` non vide / vide |
| `has_system="heating"` (ou `True`) | Bâtiment : au moins un système de ce `kind` qui le dessert, systèmes partagés compris (`serving_systems`). Site : au moins un système propre |

- Les attributs utilisables sont les attributs sourcés de la classe (y compris ceux du projet), `rnb_id`,
  les ids BDNB et `dpe_ids`.
- Un attribut inconnu lève `UnknownFieldError`. Un opérateur inconnu, un opérateur mal employé
  (`__contains` sur une valeur qui n'est pas une liste, `__isnull` sans booléen, comparaison de types
  incompatibles), une valeur d'enum invalide (`usage="education"`) ou un critère de structure inadapté au
  `kind` (`has_rnb_id` sur des sites, par exemple) lèvent `InvalidArgumentError`. `within` ou `parent`
  qui ne désigne pas un site du parc lève `UnknownIdError`, et `has_system` avec un `kind` de système
  inconnu lève `UnknownKindError`. Les critères sont contrôlés à la création de la sélection, même si le
  parc est vide.
- `has_system=False` retient les entités sans aucun système (ni propre, ni partagé pour un bâtiment).
- Une valeur absente ne satisfait aucune comparaison, sauf `__isnull=True`.
- Le prédicat reçoit l'entité. Ses exceptions ne sont pas interceptées.
- Il n'y a pas de critère sur la provenance : pour cela, on passe par
  `to_dataframe(provenance=True)` et pandas.

### 14.3 Agrégations (v3.c)

Les agrégations renvoient un DataFrame et **ne modifient jamais le modèle** : aucun candidat
`"aggregated"` n'est posé.

```python
park.select().aggregate()                           # champs extensifs, par site, avec un total parc
park.select(within="gs-a").aggregate(["heated_area"], prefer="site")
park.select().aggregate(by="usage")                 # regroupement des bâtiments par valeur effective
```

`sel.aggregate(fields=None, *, by=None, prefer="buildings")`. Par défaut, `fields` contient tous les champs
extensifs (§3.3) de `building_cls`. Un champ non extensif lève `InvalidArgumentError`.

**Le double compte.** Prenons un site « Résidence X » qui a `heated_area = 3 000 m²` (DPE immeuble), et
ses 3 bâtiments qui ont leurs propres valeurs : 1 000 + 1 200 + 700 = 2 900 m². Additionner les deux
donnerait 5 900 m², ce qui est faux. bstock ne choisit pas silencieusement : il montre les deux valeurs.

**Mode hiérarchique** (`by=None`). On a une ligne par site concerné, dans l'ordre de `iter_sites()`, puis
une ligne de total indexée `"__park__"` (un id ne peut pas contenir `__`, donc ce libellé ne se confond
jamais avec un site).
- Pour une sélection de bâtiments, les sites concernés sont les ancêtres d'au moins un bâtiment
  sélectionné, et seuls les bâtiments sélectionnés sont comptés.
- Pour une sélection de sites, ce sont les sites sélectionnés, avec tous les bâtiments de leur
  sous-arbre.

Les colonnes forment un `MultiIndex` (champ, mesure) :

| Mesure | Sens |
|---|---|
| `buildings` | Somme des valeurs effectives des bâtiments comptés du sous-arbre (valeurs connues seulement) |
| `site` | Valeur propre du site, sans héritage puisque le champ est extensif |
| `delta` | `site − buildings`, quand les deux existent |
| `coverage` | Part des bâtiments comptés qui ont une valeur (0 à 1) |
| `total` | Valeur retenue selon `prefer` (voir ci-dessous) |

Plus des colonnes de comptage, de mesure `value` : `building_count` (bâtiments comptés), `<kind>_count`
pour chaque `kind` de système (systèmes comptés, voir ci-dessous) et `meter_count` (compteurs dont le
`scope` touche un site, un bâtiment ou un système compté de la ligne). Une somme sans aucune valeur
connue est manquante (NaN), pas nulle.

**Règle `prefer`** pour `total`. Elle se calcule de bas en haut. Pour un bâtiment, `total` est sa valeur.
Pour un site :
- `prefer="buildings"` : `total` est la somme des `total` de ses enfants (bâtiments comptés et
  sous-sites). Si aucun enfant n'a de valeur, on prend la valeur propre du site.
- `prefer="site"` : `total` est la valeur propre du site si elle existe, sinon la somme des `total` de ses
  enfants.

Dans une sélection de bâtiments, la valeur propre d'un site n'entre dans un `total` que si **tous** ses
bâtiments sont sélectionnés : sinon elle couvrirait aussi des bâtiments hors sélection. Elle reste
affichée dans la mesure `site`.

La ligne `"__park__"` additionne les `total` du premier niveau compté : sites et bâtiments sans ancêtre
compté, ou sites sélectionnés sans ancêtre sélectionné. Un même bâtiment n'est donc jamais compté deux
fois dans un `total`. **Pas d'extrapolation** : une valeur manquante n'est pas estimée, et `coverage`
montre ce qui manque.

**Puissances des systèmes.** Des colonnes `heating_power`, `cooling_power` et `dhw_power` (somme des
`nominal_power`, kW) et `pv_peak_power` (somme des `peak_power`, kWp), avec les mesures `value` et
`coverage` (part des systèmes qui ont une valeur). Un système est attaché à une seule entité : il est
compté une fois, sur son porteur, et remonte dans les sites ancêtres. Il n'y a donc pas de conflit
site / bâtiments. Pour une sélection de bâtiments, un système de site est compté s'il dessert au moins un
bâtiment sélectionné.

**Regroupement par attribut** (`by="usage"`, ou tout attribut sourcé à valeur simple ; un attribut liste
lève `InvalidArgumentError`). On a une ligne par valeur effective des bâtiments sélectionnés, dans l'ordre
d'apparition (une enum par sa valeur), plus une dernière ligne `<NA>` pour les bâtiments sans valeur. Les mesures sont
`buildings`, `coverage` et `building_count`. Les valeurs propres des sites sont ignorées, parce qu'elles
ne se rattachent à aucun groupe. Si un site concerné en a une pour un champ agrégé, un `NoDataWarning` le
signale. `by` n'est accepté que pour une sélection de bâtiments.

### 14.4 Complétude et provenance (v3.c)

```python
sel.completeness()                    # par attribut
sel.completeness("entity")            # par entité
sel.provenance(normalize=True)        # répartition des sources
sel.conflicts(rtol=0.05)              # candidats en désaccord
```

Par défaut, `fields` contient tous les attributs sourcés de la classe des entités sélectionnées (projet
compris, `footprint` compris). L'enveloppe et les systèmes ne sont pas couverts en v3.

Signatures : `completeness(by="attribute", *, fields=None)`, `provenance(fields=None, *,
normalize=False)`, `conflicts(fields=None, *, rtol=0.05)`. Un attribut inconnu lève `UnknownFieldError`.
Une entité dont la classe ne déclare pas un attribut (champ d'une autre sous-classe projet) n'est pas
comptée pour lui. Un taux sans entité concernée est manquant (NaN).

**`completeness(by="attribute", fields=None)`.** Une valeur héritée compte comme renseignée.
- `by="attribute"` : une ligne par attribut. Colonnes `filled` (nombre d'entités avec une valeur
  effective), `inherited` (dont valeurs héritées), `total` et `rate` (`filled / total`).
- `by="entity"` : une ligne par entité. Colonnes `filled`, `total`, `rate` et `missing` (liste des
  attributs sans valeur).

**`provenance(fields=None, normalize=False)`.** Une ligne par attribut et une colonne par source retenue
(nombre d'entités dont la valeur effective vient de cette source), la source la plus fréquente sur
l'ensemble des attributs en premier, puis par ordre alphabétique. S'y ajoutent les colonnes `inherited`
(dont valeurs héritées, toutes sources confondues) et `missing`. Avec `normalize=True`, on obtient des
parts plutôt que des nombres.

**`conflicts(fields=None, rtol=0.05)`.** Une ligne par entité et par attribut dont les **candidats
propres** ne sont pas d'accord. Les valeurs héritées ne sont pas comparées : un autre niveau n'est pas un
conflit.
- Les chaînes, les enums, les listes et les entiers sont comparés strictement. Une année 1970 contre 1972
  est donc un conflit.
- Les flottants sont comparés avec la tolérance relative `rtol` (rapportée à la plus grande valeur
  absolue).
- Colonnes : `entity`, `attribute`, `values` (dict source → valeur, enums par leur valeur), `retained`
  (source retenue). Sans conflit, le DataFrame est vide avec ces colonnes.
- Par défaut, `footprint` n'est pas comparé : deux emprises de sources différentes (RNB, BDNB) ne sont
  presque jamais identiques au point près. On le compare en le nommant dans `fields`.

### 14.5 Graphiques (v3.e)

Les graphiques sont des méthodes de l'accesseur `sel.plot`, dans le style de `df.plot` de pandas. Le code
est dans `plot.py`. plotly (extra `[plot]`) est importé à l'appel ; sans lui, l'appel lève
`MissingDependencyError`. Chaque méthode **renvoie une `plotly.graph_objects.Figure`** sans l'afficher.
On la personnalise ensuite avec `fig.update_layout(...)`.

| Méthode | Graphique | Données |
|---|---|---|
| `plot.completeness(by="attribute", *, fields=None)` | Barres horizontales du taux, avec la part héritée en couleur distincte | §14.4 |
| `plot.provenance(fields=None)` | Barres empilées des sources par attribut (parts) | §14.4 |
| `plot.aggregate(field, *, by=None, prefer="buildings")` | Barres de `total` par site (ou par groupe `by`), avec la couverture au survol | §14.3 |
| `plot.distribution(field, *, bins=None)` | Histogramme pour un nombre ; barres de fréquence pour une enum ou une chaîne | Valeurs effectives |
| `plot.map(*, geometry="point", color=None)` | Carte sur fond OpenStreetMap (sans clé d'API) : points `lat` / `lon` ou emprises `footprint` (GeoJSON WGS84, sans l'extra `[geo]`), colorés par un attribut | Valeurs effectives |
| `plot.consumption(*, start, end, freq=None, by="energy_type", **kwargs)` | Barres empilées de la consommation (kWh) par entité, empilées par énergie (`by="energy_type"`) ou par usage (`by="end_use"`) ; une barre par période avec `freq` | §6.5 |
| `plot.intensity(*, start, end, area="heated_area", freq=None, **kwargs)` | Barres du kWh/m² par entité, empilées par énergie | §6.5 |

Une entité sans valeur n'est pas tracée, et le titre indique la couverture (« 34 / 42 bâtiments »). Les
unités des champs (métadonnée `unit`) étiquettent les axes.

Précisions (implémentation 0.8.0) :
- plotly ≥ 5.24 (cartes `Scattermap` / `Choroplethmap` sur fond MapLibre, sans jeton). `**kwargs` de
  `consumption` et `intensity` sont transmis à `Selection.consumption` / `intensity` (`tz`, `breakdown`,
  `split`, `load_data`, `prefer`).
- Couleurs : huit teintes catégorielles dans un ordre fixe, validé pour les daltonismes (paires
  adjacentes) ; au-delà, les catégories se replient en « Other » gris. Sur une carte, où toutes les
  couleurs se côtoient, seules les **trois** valeurs les plus fréquentes ont une couleur (à égalité,
  l'ordre de la sélection), le reste est « Other ». Les nombres utilisent une rampe bleue unique. Les
  valeurs manquantes sont grises. Une couleur suit la catégorie, jamais son rang dans un filtre.
- Style sobre : grille sur l'axe des valeurs seulement, barres arrondies séparées par un filet de la
  couleur du fond, légende en haut dans l'ordre des traces, survol avec valeur, unité et couverture.
- `plot.consumption(freq=...)` : une barre par période, qui additionne les entités sélectionnées (dans
  une sélection de sites, des sites imbriqués se recouvrent). `plot.intensity(freq=...)` : par période,
  énergie des lignes qui ont une surface divisée par la somme de leurs surfaces (indicateur de la
  sélection, pas somme de ratios).
- `plot.distribution` d'un attribut liste ou dict lève `InvalidArgumentError` ; un attribut inconnu lève
  `UnknownFieldError`, comme `color=` de `plot.map`.

---

## Annexe A. Correspondances avec les sources (base des connecteurs v2)

Sources consultées le 2026-10-13 :
- métadonnées de l'API BDNB (`api.bdnb.io/v1/bdnb/metadonnees/colonne`, millésime 2026-02.a) ;
- schéma du jeu ADEME `dpe03existant` (DPE logements existants depuis juillet 2021) et `dpe01tertiaire` ;
- tables d'enums de l'Observatoire DPE (gitlab.com/observatoire-dpe/observatoire-dpe, `modele_donnee/`).

### A.1 Ce que fournissent les sources

| Information | BDNB | Open data DPE | XML / dump DPE |
|---|---|---|---|
| Lien RNB | `batiment_construction.rnb_id` (niveau construction) | `id_rnb` (~50 % des DPE) | |
| Lien BDNB | `batiment_groupe_id` | absent | |
| Usage, année, logements | niveau **groupe** (`usage_principal_bdnb_open`, `annee_construction`, `nb_log`) | `periode_construction`, `nombre_appartement` | |
| Emprise, hauteur | groupe (`s_geom_groupe`, `hauteur_mean`) et construction (`s_geom_cstr`, `hauteur`) | | |
| Surfaces | pas de surface chauffée ni de plancher | `surface_habitable_logement/immeuble`, `surface_utile` (tertiaire) | |
| Enveloppe par poste | | **déperditions en W/K** (`deperditions_murs`, `_planchers_hauts`, `_planchers_bas`, `_baies_vitrees`, `_portes`, `_ponts_thermiques`), `ubat_w_par_m2_k`, `qualite_isolation_*`, `classe_inertie_batiment` | surfaces, U, orientation, vitrage, menuiserie, Sw par paroi |
| Q4Pa | | | `q4pa_conv` |
| Générateurs | type par libellé (37 chauffage, 31 ECS, 6 froid) | `type_generateur_*` (171 types chauffage, 134 ECS), `type_energie_*` | `rendement_generation`, `scop`, `cop` |
| DPE tertiaire | énergie seulement | consommations, `surface_utile`, presque rien sur l'enveloppe | |

### A.2 BuildingUsage ← BDNB `usage_principal_bdnb_open`

| BDNB | bstock |
|---|---|
| `Résidentiel individuel` | `RESIDENTIAL_INDIVIDUAL` |
| `Résidentiel collectif` | `RESIDENTIAL_COLLECTIVE` |
| `Tertiaire` | `TERTIARY` |
| `Secondaire` | `SECONDARY` |
| `Primaire` | `PRIMARY` |
| `Dépendance` | `OUTBUILDING` |
| `Indifférencié` | `UNDETERMINED` |

### A.3 EnergyType

| BDNB (`type_energie_*`) | DPE (`type_energie_*`) | bstock |
|---|---|---|
| `electricite` | `Électricité`, `Électricité d'origine renouvelable utilisée dans le bâtiment` | `ELECTRICITY` |
| `gaz` | `Gaz naturel` | `NATURAL_GAS` |
| `fioul` | `Fioul domestique` | `FUEL_OIL` |
| `gpl/butane/propane` | `GPL`, `Propane`, `Butane` | `LPG` |
| `bois` | | `WOOD` |
| | `Bois – Bûches` | `WOOD_LOGS` |
| | `Bois – Granulés (pellets) ou briquettes` | `WOOD_PELLETS` |
| | `Bois – Plaquettes forestières`, `Bois – Plaquettes d’industrie` | `WOOD_CHIPS` |
| `charbon` | `Charbon` | `COAL` |
| `reseau de chaleur` | `Réseau de Chauffage urbain` | `DISTRICT_HEATING` |
| `reseau de froid` | `Réseau de Froid Urbain` | `DISTRICT_COOLING` |
| `solaire` | | `SOLAR` |
| `autre combustible fossile` (tertiaire) | `autre combustible fossile` | `OTHER_FOSSIL` |

Attention aux caractères : les libellés bois du DPE utilisent un tiret demi-cadratin (U+2013), et
« d’industrie » une apostrophe typographique.

### A.4 Enveloppe (DPE)

| DPE | bstock |
|---|---|
| `qualite_isolation_*` : `très bonne`, `bonne`, `moyenne`, `insuffisante` | `InsulationLevel` : `VERY_GOOD`, `GOOD`, `AVERAGE`, `INSUFFICIENT` |
| `classe_inertie_batiment` : `Très lourde`, `Lourde`, `Moyenne`, `Légère` | `Inertia` : `VERY_HEAVY`, `HEAVY`, `MEDIUM`, `LIGHT` |
| `type_vitrage` : Simple, Double, Triple vitrage, Survitrage, Brique de Verre, Polycarbonate | `GlazingType` |
| `type_materiaux_menuiserie` : Bois, Bois/métal, PVC, Métal avec / sans rupture de pont thermique (Brique de verre, Polycarbonate → `OTHER`) | `FrameType` |
| `deperditions_murs`, `_planchers_hauts`, `_planchers_bas`, `_baies_vitrees`, `_portes`, `_ponts_thermiques` | un `EnvelopeComponent` par poste, avec `heat_loss_coefficient` |
| `ubat_w_par_m2_k` | `Envelope.u_bat` |

À noter : la clé de l'API est `qualite_isolation_plancher_bas` ; l'espace n'existe que dans le nom
d'origine (`x-originalName`). Les connecteurs utilisent toujours les clés de l'API.

### A.5 GeneratorTechnology

Les sources donnent souvent un type sans détail (« chaudière gaz indéterminée », « PAC autre que
air/air ») : il est traduit par une technologie générique (`BOILER`, `HEAT_PUMP`, `WATER_HEATER`), qui
garde la famille de l'équipement. `raw` conserve toujours le libellé d'origine.

**BDNB** (`type_generateur_chauffage[_appoint]`, `type_generateur_ecs[_appoint]`,
`type_generateur_climatisation`). Le libellé porte la technologie et souvent l'énergie ; la colonne
`type_energie_*` correspondante, quand elle existe, l'emporte sur l'énergie lue dans le libellé.

| Libellé BDNB | `technology` | `energy_type` |
|---|---|---|
| `chaudiere <énergie> standard` | `BOILER_STANDARD` | selon `<énergie>` |
| `chaudiere <énergie> basse temperature` | `BOILER_LOW_TEMPERATURE` | selon `<énergie>` |
| `chaudiere <énergie> condensation` | `BOILER_CONDENSING` | selon `<énergie>` |
| `chaudiere <énergie> indetermine`, `chaudiere bois` | `BOILER` | selon `<énergie>` |
| `chaudiere electrique` | `ELECTRIC_BOILER` | `ELECTRICITY` |
| `pac air/air`, `pac air/eau`, `pac eau/eau`, `pac geothermique`, `pac gaz` | `HEAT_PUMP_AIR_AIR`, `_AIR_WATER`, `_WATER_WATER`, `_GROUND`, `_GAS` | électricité (gaz naturel pour `pac gaz`) |
| `pac indetermine` | `HEAT_PUMP` | `ELECTRICITY` |
| `poele ou insert <énergie>`, `poele bouilleur bois` | `STOVE_INSERT` | selon `<énergie>` |
| `radiateurs gaz` | `GAS_RADIATOR` | `NATURAL_GAS` |
| `generateur air chaud combustion` | `WARM_AIR_GENERATOR` | – |
| `generateurs a effet joule` | `ELECTRIC_RESISTANCE` | `ELECTRICITY` |
| `reseau de chaleur` / `reseau de froid` | `DISTRICT_SUBSTATION` | `DISTRICT_HEATING` / `DISTRICT_COOLING` |
| `chauffage solaire`, `ecs solaire` | `SOLAR_THERMAL` | `SOLAR` |
| `ballon accumulation electrique` | `ELECTRIC_WATER_TANK` | `ELECTRICITY` |
| `chauffe eau electrique instantane` | `INSTANT_WATER_HEATER` | `ELECTRICITY` |
| `ecs thermodynamique electrique(pac ou ballon)` | `THERMODYNAMIC_WATER_HEATER` | `ELECTRICITY` |
| `chauffe-eau <énergie> independant` | `WATER_HEATER` | selon `<énergie>` |
| `chauffage bois indetermine`, `ecs bois indetermine` | `OTHER` | `WOOD` |
| `chauffage autre indetermine`, `ecs autre indetermine`, `production mixte indetermine` | `OTHER` | – |

`<énergie>` : `gaz` → `NATURAL_GAS`, `fioul` → `FUEL_OIL`, `gpl/butane/propane` → `LPG`, `bois` →
`WOOD`, `charbon` → `COAL`, `energie indetermine` ou `independant indetermine` → pas de valeur.

**DPE** (`type_generateur_*` de l'open data, 171 libellés de chauffage et 134 d'ECS, avec la période
d'installation : « Chaudière gaz à condensation après 2015 »). La correspondance est une **table de
données livrée avec bstock** (`connectors/data/dpe_generators.csv` : libellé → `technology`,
`energy_type`), relue ligne par ligne. Elle est générée une fois, par un script fourni dans le dépôt, à
partir des tables d'enums de l'Observatoire DPE (feuilles `type_generateur_ch` et `type_generateur_ecs`
de `modele_donnee/enum_tables.xlsx`, gitlab.com/observatoire-dpe/observatoire-dpe, Licence Ouverte 2.0 :
source et date citées dans l'en-tête du fichier), en s'aidant de leur colonne `categorie_open_data`.
La comparaison des libellés est exacte, sans normalisation. Un libellé absent de la table, par exemple
ajouté plus tard par l'ADEME, donne `OTHER` et un `UnmappedValueWarning` jusqu'à la mise à jour de la
table. L'énergie vient de la colonne `type_energie_generateur_*` (table A.3).

Contenu de la table (210 libellés distincts, version 2.6 des tables d'enums, générée le 2026-10-14) :
- elle couvre aussi la feuille `type_generateur_fr` (froid, 23 libellés, colonne `type_generateur_froid`
  de l'open data). Un libellé présent dans plusieurs feuilles n'a qu'une ligne ; la colonne `sheets` les
  cite, et `categorie_open_data` est gardée pour la relecture ;
- `energy_type` est l'énergie impliquée par le générateur, déduite des énergies autorisées
  (`enum_type_energie_id`, union des feuilles) : `autre combustible fossile` et le réseau de chaleur des
  générateurs « multi bâtiment » sont ignorés à côté d'une autre énergie, plusieurs formes de bois
  donnent `WOOD`, et plusieurs énergies distinctes laissent la case vide (un poêle bûche peut brûler du
  charbon). Les PAC du froid reçoivent `ELECTRICITY` ;
- pompe à chaleur hybride : la partie PAC donne `HEAT_PUMP_HYBRID`, la partie chaudière la technologie
  de cette chaudière (le DPE les décrit comme deux générateurs) ;
- accumulateurs gaz ou GPL → `WATER_HEATER` ; chauffe-eau gaz à production instantanée →
  `INSTANT_WATER_HEATER` ; PAC eau glycolée/eau → `HEAT_PUMP_GROUND` ; PAC double service → `HEAT_PUMP` ;
  chaudières bois et charbon (type non précisé) → `BOILER` ; « autre système à combustion … » et
  « autre système de refroidissement » → `OTHER` ; « autre système thermodynamique gaz » →
  `HEAT_PUMP_GAS`.

### A.6 Contrats des API (vérifiés le 2026-10-13)

Réponses réelles enregistrées dans `tests/data/` lors de l'écriture de chaque connecteur.

**RNB** : `https://rnb-api.beta.gouv.fr/api/alpha/` (schéma OpenAPI : `schema/`), lecture sans
authentification, limite de 20 requêtes par seconde (429 au-delà). Toujours terminer les chemins par `/`.

| Endpoint | Usage | Remarques |
|---|---|---|
| `buildings/{rnb_id}/` | Un bâtiment | 404 si inconnu. Champs : `rnb_id`, `status` (`constructed`, `notUsable`, `demolished`), `point`, `shape`, `addresses`, `ext_ids`, `is_active`, `validated_by` |
| `buildings/?insee_code=` ou `?bbox=lon_min,lat_min,lon_max,lat_max` | Liste | Un filtre obligatoire ; pas de filtre par liste d'ids. Bâtiments démolis exclus sauf `status=constructed,notUsable,demolished` ; ids inactifs exclus |
| `buildings/closest/?point=lat,lon&radius=` | Suggestions par point | **lat,lon** (l'inverse des géométries) ; un ordre inversé renvoie une liste vide sans erreur. `radius` obligatoire, ≤ 1000 m. Résultats triés par `distance` (m), sans `is_active` |
| `buildings/address/?q=&min_score=` | Suggestions par adresse | Géocodage BAN interne, meilleure adresse seulement : `status` (`ok`, `geocoding_score_is_too_low`, `geocoding_no_result`), `cle_interop_ban`, `score_ban`, `results` (`null` en cas d'échec) |
| `buildings/{rnb_id}/history/` | Successeurs d'un id inactif | Non documenté. Versions de la plus récente à la plus ancienne ; `event.type` ∈ `creation`, `update`, `merge`, `split`, `deactivation` ; `event.details.merge_child` ou `split_children` |

- Pagination par curseur : `{"next", "previous", "results"}`, paramètre `limit` (20 par défaut, 100 au
  plus). On suit `next`.
- Géométries GeoJSON en WGS84, coordonnées `[lon, lat]`. `point` est un `Point` ; `shape` est le plus
  souvent un `MultiPolygon` mais peut être un `Point`.
- `addresses[]` : `id` (clé d'interopérabilité BAN, ex. `64102_0885_00004`), `street_number` (chaîne),
  `street_rep`, `street`, `city_name`, `city_zipcode`, `city_insee_code`. La liste peut être vide.
- `ext_ids[]` : `{id, source, source_version, created_at}` ; `source = "bdnb"` donne un
  `bdnb-bc-XXXX-XXXX-XXXX` (version `2023_01`).
- Un id inactif (fusion, scission, désactivation) répond 200 avec `is_active: false`, sans successeur
  dans la réponse. Les ids sont acceptés en minuscules ou avec tirets ; la réponse les donne en
  majuscules sur 12 caractères.
- Messages d'erreur en français, au format Django REST Framework (`detail`, `non_field_errors`).

**BDNB** : `https://api.bdnb.io/v1/bdnb/donnees/<table>` (PostgREST derrière une passerelle Gravitee),
métadonnées sous `metadonnees/<table>` (`colonne`, `jeu_de_donnees`). Seul le dernier millésime est servi
(2026-02.a au 2026-10-13).

| Offre | Clé (`X-Gravitee-Api-Key`) | Lignes par appel | Appels | Débit |
|---|---|---|---|---|
| Open | aucune | 10 | 10 000 / mois / IP | 120 / min |
| Open+ | gratuite jusqu'à 100 000 appels / an | 1 000 | 1 M / mois | 1 200 / min |
| Expert | payante | 10 000 | 10 M / mois | 1 200 / min |

| Table | Usage | Colonnes utiles |
|---|---|---|
| `batiment_construction` | `rnb_id` → construction et groupe ; constructions d'un groupe | `batiment_construction_id`, `batiment_groupe_id`, `rnb_id` (souvent vide), `s_geom_cstr` (m², entier), `hauteur` (m), `geom_cstr` (MultiPolygon, EPSG:2154) |
| `batiment_groupe_complet` | Valeurs du groupe | `usage_principal_bdnb_open`, `annee_construction`, `nb_log`, `nb_niveau` (RDC = 1), `hauteur_mean` (m), `s_geom_groupe` (m²), `geom_groupe` (EPSG:2154), `type_generateur_chauffage[_appoint]`, `type_energie_chauffage[_appoint]`, `type_generateur_ecs[_appoint]`, `type_generateur_climatisation`, `type_installation_chauffage` / `_ecs` (`individuel`, `collectif`), `identifiant_dpe`, `type_dpe`, `date_reception_dpe` |
| `batiment_groupe_dpe_representatif_logement` | Énergies d'ECS et de froid, date du DPE représentatif | `type_energie_ecs[_appoint]`, `type_energie_climatisation`, `date_etablissement_dpe`, `nb_generateur_chauffage`, `nb_generateur_ecs` |

- Filtres PostgREST : `col=eq.x`, `col=in.(a,b)`, `select=a,b`, `order=`, `limit` / `offset` (plafonnés
  sans erreur à la taille de l'offre). `Prefer: count=exact` donne le total dans `Content-Range` : c'est
  ainsi qu'on compte les constructions d'un groupe en un appel. Les agrégats sont refusés.
- Géométries en GeoJSON avec un membre `crs` (EPSG:2154), même avec `Accept: application/geo+json` :
  reprojection côté client.
- Quotas dans les en-têtes `X-Quota-Remaining` et `X-Rate-Limit-Remaining` ; 429 au-delà. Une requête
  sur une colonne non indexée peut dépasser le délai de 10 s (erreur Postgres 57014) : filtrer sur les
  ids.
- Erreurs PostgREST `{code, details, hint, message}`. La route `batiment_groupe_complet/rnb` citée dans
  la documentation n'existe pas.
- Les deux colonnes d'usage peuvent diverger (`usage_principal_bdnb_open` contre `usage_niveau_1_txt`
  des fichiers fonciers) : bstock utilise la première.

**DPE (ADEME)** : `https://data.ademe.fr/data-fair/api/v1/datasets/<jeu>/lines`, jeux `dpe03existant`
(logements existants depuis juillet 2021), `dpe02neuf` et `dpe01tertiaire`. Accès libre ; les DPE
désactivés sont déjà filtrés.

- Paramètres : `select=`, `size` (≤ 10 000), `sort=`, `count=false` (économise le budget), filtres par
  colonne `<col>_eq`, `_in` (valeurs séparées par des virgules), `_gte`, `_lte`, `_exists`, et `qs`
  (syntaxe Lucene). Au-delà de 10 000 lignes, on suit `next` (paramètre `after`).
- Limite : un budget de temps de traitement cumulé, sans en-tête de quota ; un dépassement renvoie un
  message en français (« Le temps de traitement cumulé de vos requêtes dépasse le budget alloué… »). Le
  client espace ses appels et réessaie avec attente.
- Les colonnes vides sont **absentes** des lignes. Les dates sont au format ISO `YYYY-MM-DD` (contrairement
  au `dateFormat` du schéma). `Non affecté` sert de valeur vide.
- Colonnes communes : `numero_dpe` (5e caractère : `E` existant, `N` neuf, `T` tertiaire),
  `numero_dpe_remplace`, `numero_dpe_immeuble_associe`, `id_rnb`, `date_etablissement_dpe`,
  `identifiant_ban`, `code_insee_ban`, `annee_construction`, `periode_construction`.
- Échelle : `type_batiment` ∈ `appartement`, `maison`, `immeuble` (logement) ; `methode_dpe` (tertiaire,
  « … dans un bâtiment de logement » pour un local dans un immeuble de logements).
- Logement : `surface_habitable_logement`, `surface_habitable_immeuble`, `nombre_appartement`,
  `nombre_niveau_immeuble`, `ubat_w_par_m2_k`, `classe_inertie_batiment`, `qualite_isolation_murs`,
  `_menuiseries`, `_plancher_bas`, `_plancher_haut_comble_perdu`, `_plancher_haut_comble_amenage`,
  `_plancher_haut_toit_terrasse`, `deperditions_murs`, `_planchers_hauts`, `_planchers_bas`,
  `_baies_vitrees`, `_portes`, `_ponts_thermiques` (W/K : unité non déclarée par le schéma, déduite des
  valeurs et de la documentation de l'Observatoire DPE).
- Générateurs de chauffage : `type_generateur_n{1,2}_installation_n{1,2}`,
  `type_energie_generateur_n{1,2}_installation_n{1,2}`, `usage_generateur_…` (`chauffage`, `ecs`,
  `chauffage + ecs`). ECS : `type_generateur_n{1,2}_ecs_n1`, `type_energie_generateur_n{1,2}_ecs_n1`,
  `volume_stockage_generateur_n{1,2}_ecs_n1` (L, 0 pour une chaudière mixte), `cop_generateur_n{1,2}_ecs_n1`.
  Pas de colonne de nombre de générateurs : on compte les emplacements renseignés. Dans `dpe02neuf`, ces
  colonnes sont presque toujours vides.
- Tertiaire : `surface_utile`, `surface_shon`, `secteur_activite` (32 valeurs, codes ERP),
  `type_energie_principale_chauffage` ; pas de colonnes d'enveloppe ni de systèmes.
- Les libellés de générateurs de l'API sont identiques, caractère pour caractère, aux libellés des tables
  d'enums de l'Observatoire DPE (`modele_donnee/enum_tables.xlsx`, feuilles `type_generateur_ch` : 171
  lignes, `type_generateur_ecs` : 134 lignes), qui portent la colonne `categorie_open_data`, absente de
  l'API. Ne pas normaliser les apostrophes avant de comparer : les libellés mélangent `'` et `’`.
