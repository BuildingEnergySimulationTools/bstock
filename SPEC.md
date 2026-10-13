# bstock — Spécification

> Statut : **v0.3**, issue de l'interview de conception du 2026-10-13. Les choix sont validés ; les points
> encore ouverts sont listés au §11.

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
- **Simple à l'usage.** `building.year_construction` renvoie directement une valeur ; la provenance est
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
| **DataSource** | Description de l'endroit où lire les données d'un Meter (CSV, Parquet, API…). |
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
 │   └─ source: DataSource | None
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
  - systèmes, parois et Meters : choisis par l'utilisateur ou générés automatiquement (préfixe + 8
    caractères hexadécimaux, ex. `"heating-1f3a9c0b"`).
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

- **Unités SI fixées par champ**, sans bibliothèque d'unités. L'unité est déclarée dans les métadonnées du
  champ : elle apparaît dans le JSON Schema, dans `Building.sourced_fields()["heated_area"].unit` et dans
  le tag `unit` de tide.
- Les attributs **descriptifs** sont « sourcés » (§4). Les **identifiants et relations** (`id`, `rnb_id`,
  listes d'enfants, `scope`, `serves`) sont des valeurs simples.
- Tous les attributs sourcés sont optionnels : une valeur inconnue vaut `None`.
- **Les collections (sites, bâtiments, systèmes, parois, compteurs) se modifient uniquement via les
  méthodes du parc ou des entités** (`new_*`, `add_*`, `remove`, `move`). Ces méthodes maintiennent l'index
  des ids, les liens vers le parent et les contrôles de cohérence. La modification directe des listes
  n'est pas supportée.

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
| `new_site(id, parent=None, source=None, **attrs)` | Crée un site (instance de `site_cls`) au premier niveau ou sous `parent` |
| `new_building(rnb_id=None, id=None, site=None, source=None, **attrs)` | Crée un bâtiment (instance de `building_cls`) |
| `add_site(site, parent=None)`, `add_building(building, site=None)` | Ajoute un objet déjà construit (contrôle de classe et d'unicité des ids) |
| `add_system(system, owner)` | Ajoute un système à un bâtiment ou à un site (`owner` = objet ou id) |
| `add_meter(meter)` | Ajoute un compteur ; les ids du `scope` doivent exister |
| `get(id)`, `get_by_rnb(rnb_id)` | Recherche |
| `iter_sites()`, `iter_buildings()`, `iter_systems()` | Parcours de tout l'arbre |
| `move(id, new_parent)` | Déplace un site ou un bâtiment (`new_parent` = site, ou `None` pour le premier niveau) |
| `remove(id)` | Supprime une entité. Lève une erreur si elle est référencée (`scope` d'un Meter, `serves` d'un système), sauf avec `force=True`, qui retire aussi les références |

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

Accès dérivés (non sérialisés) : `site.parent` (site parent ou `None`), `site.park`,
`site.iter_buildings(recursive=True)`, `site.iter_sites(recursive=True)`.

### 3.6 Building

| Attribut | Type | Unité | Description |
|---|---|---|---|
| `id` | `str` | | Id interne stable (§3.2) |
| `rnb_id` | `str \| None` | | Identifiant RNB, unique dans le parc |
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
éviter les doubles comptes.

| Attribut | Type | Unité | Hérité | Description |
|---|---|---|---|---|
| `name` | sourcé `str` | | non | |
| `address` | sourcé `str` | | oui | |
| `municipality` | sourcé `str` | | oui | Nom de la commune |
| `insee_code` | sourcé `str` | | oui | Code INSEE de la commune |
| `lat`, `lon` | sourcé `float` | ° (WGS84) | oui | Point représentatif |
| `usage` | sourcé `BuildingUsage` | | oui | Usage principal (§3.9) |
| `sub_usages` | sourcé `list[str]` | | oui | Remplace `sub_usage_category_1..3` |
| `year_construction` | sourcé `int` | | oui | |
| `nb_levels` | sourcé `int` | | oui | Nombre de niveaux, RDC = 1 (fusion de `floor_number` et `nb_level`) |
| `height` | sourcé `float` | m | oui | |
| `nb_dwellings` | sourcé `int` | | **non** | Nombre de logements |
| `footprint_area` | sourcé `float` | m² | **non** | Emprise au sol |
| `floor_area` | sourcé `float` | m² | **non** | Surface de plancher (`floor_surface` du draw.io) |
| `heated_area` | sourcé `float` | m² | **non** | Surface chauffée |

Accès dérivés du bâtiment (non sérialisés) :
- `building.site` : le site parent direct ou `None` ;
- `building.park` : le parc ;
- `building.systems(kind="heating", include_shared=True)` : systèmes propres, plus systèmes des sites
  ancêtres qui le desservent ;
- `building.meters()` : compteurs dont le `scope` contient ce bâtiment ou un de ses systèmes.

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
env.components_by(kind="window", orientation="s")   # filtrage
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
- Un système porté par un bâtiment dessert implicitement ce seul bâtiment.
- Les ids de `serves` doivent désigner des bâtiments du site ou de ses sous-sites (vérifié par le parc).

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
sous-station, ou un ratio < 1 pour une PAC, un groupe froid ou un chauffe-eau thermodynamique. Les PAC
hybrides et le solaire thermique ne sont pas contrôlés.

Chaque classe de système a un `kind` (`heating`, `cooling`, `dhw`, `ventilation`, `pv`) qui sert de préfixe
aux ids générés et de filtre dans `building.systems(kind=...)`.

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
Elles sont sérialisées par cette valeur, et les API acceptent indifféremment le membre ou la chaîne.

| Enum | Valeurs |
|---|---|
| `BuildingUsage` | `RESIDENTIAL_INDIVIDUAL`, `RESIDENTIAL_COLLECTIVE`, `TERTIARY`, `SECONDARY`, `PRIMARY`, `OUTBUILDING`, `UNDETERMINED` (les 7 valeurs de `usage_principal_bdnb_open`) |
| `EnergyType` | `ELECTRICITY`, `NATURAL_GAS`, `FUEL_OIL`, `LPG` (GPL, propane, butane), `WOOD`, `WOOD_LOGS`, `WOOD_PELLETS`, `WOOD_CHIPS`, `COAL`, `DISTRICT_HEATING`, `DISTRICT_COOLING`, `SOLAR`, `OTHER_FOSSIL`, `OTHER` |
| `GeneratorTechnology` | `BOILER_STANDARD`, `BOILER_LOW_TEMPERATURE`, `BOILER_CONDENSING`, `ELECTRIC_BOILER`, `ELECTRIC_RESISTANCE` (effet Joule), `ELECTRIC_STORAGE_HEATER`, `HEAT_PUMP_AIR_AIR`, `HEAT_PUMP_AIR_WATER`, `HEAT_PUMP_WATER_WATER`, `HEAT_PUMP_GROUND`, `HEAT_PUMP_GAS`, `HEAT_PUMP_HYBRID`, `STOVE_INSERT`, `GAS_RADIATOR`, `WARM_AIR_GENERATOR`, `DISTRICT_SUBSTATION`, `SOLAR_THERMAL`, `ELECTRIC_WATER_TANK`, `INSTANT_WATER_HEATER`, `THERMODYNAMIC_WATER_HEATER`, `CHILLER`, `OTHER` |
| `Inertia` | `LIGHT`, `MEDIUM`, `HEAVY`, `VERY_HEAVY` (classes du DPE) |
| `InsulationLevel` | `INSUFFICIENT`, `AVERAGE`, `GOOD`, `VERY_GOOD` (les 4 valeurs du DPE) |
| `GlazingType` | `SINGLE`, `DOUBLE`, `TRIPLE`, `SECONDARY_GLAZING` (survitrage), `GLASS_BRICK`, `POLYCARBONATE` |
| `FrameType` | `WOOD`, `WOOD_METAL`, `PVC`, `METAL_THERMAL_BREAK`, `METAL_NO_THERMAL_BREAK`, `OTHER` |
| `ComponentKind` | `WALL`, `ROOF`, `FLOOR`, `WINDOW`, `DOOR`, `THERMAL_BRIDGE` |
| `Orientation` | `N`, `NE`, `E`, `SE`, `S`, `SW`, `W`, `NW`, `HORIZONTAL` (le DPE n'utilise que N, E, S, O, horizontal) |
| `Adjacency` | `EXTERIOR`, `UNHEATED_SPACE`, `GROUND`, `ADJACENT_BUILDING` |
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
b.year_construction                    # -> 1972 (valeur effective) ou None
b.year_construction = 1968             # équivaut à b.set(..., source="manual")
b.year_construction = None             # retire le candidat "manual" (équivaut à unset(source="manual"))

b.set("year_construction", 1970, source="bdnb", date=date(2024, 10, 1))
b.set("year_construction", 1972, source="dpe", ref="2364E0123456X")

b.sources("year_construction")
# [SourcedValue(1972, source="dpe", ref="2364E0123456X"),
#  SourcedValue(1970, source="bdnb", date=2024-10-01)]

b.effective("year_construction")       # -> SourcedValue retenue (avec sa source)
b.unset("year_construction", source="bdnb")   # retire le candidat d'une source
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
  park.new_building(rnb_id="...", source="bdnb", year_construction=1970, height=12.5)
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
site.set("year_construction", 1972, source="bdnb")   # posé sur le groupe BDNB
b.year_construction                           # -> 1972 (hérité)
b.origin("year_construction")                 # -> "bdnb-bg-1111-1HRT-8SVH" (id de l'entité qui fournit la valeur)
b.sources("year_construction")                # -> [] (candidats propres seulement)
b.sources("year_construction", inherited=True)
# [("bdnb-bg-1111-1HRT-8SVH", SourcedValue(1972, source="bdnb"))]
b.set("year_construction", 1985, source="audit")   # valeur propre : masque l'héritage
```

- Les grandeurs extensives (`nb_dwellings`, surfaces, `heat_loss_coefficient`…) ne sont **jamais**
  héritées. Une valeur posée sur un site vaut pour le site entier. Les agrégations (somme des bâtiments
  comparée à la valeur du site) sont prévues en v3.
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
      year_construction = sourced(int, inherit=True)
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
- L'héritage des valeurs repose sur deux méthodes surchargées par les entités :
  `inheritance_parent()` renvoie l'objet dont on hérite (le site parent pour un site ou un bâtiment,
  l'enveloppe du site parent pour une enveloppe), et `entity_id()` renvoie l'id affiché par `origin()` (une
  enveloppe renvoie l'id de son propriétaire). L'héritage s'arrête au premier ancêtre qui ne déclare pas
  l'attribut.

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
        park.new_building(rnb_id=row.rnb, site=site, source="bayonne_xlsx_2024",
                          code_patrimoine=row.code, surface_utile_client=row.su)
```

Règles :
- `park.new_site(id, parent=None, ...)` crée un site de premier niveau ou un sous-site.
- `park.new_site(...)` et `park.new_building(...)` instancient `park.site_cls` et `park.building_cls`. Les
  futurs connecteurs RNB, BDNB et DPE passeront aussi par ces méthodes : ils produiront donc des
  `BayonneBuilding` sans rien connaître de Bayonne.
- `park.add_building(b, site=None)` et `park.add_site(s)` vérifient que l'objet est une instance de la
  classe configurée.
- **Chargement** : `BuildingPark.load(path, site_cls=BayonneSite, building_cls=BayonneBuilding)`. Si un
  fichier contient des attributs inconnus de la classe utilisée (par exemple, un parc Bayonne rechargé avec
  `Building`), ils sont **conservés tels quels** et réécrits à la sauvegarde. Un avertissement est émis :
  pas de perte de données.
- Le JSON enregistre le chemin des classes utilisées (`"building_cls": "projet_bayonne.model.BayonneBuilding"`)
  à titre informatif. Un avertissement est émis si la classe passée au chargement diffère.

---

## 6. Compteurs et séries temporelles

### 6.1 Meter

| Attribut | Type | Description |
|---|---|---|
| `id` | `str` | Identifiant unique (ex. numéro de PDL) |
| `name` | sourcé `str` | |
| `scope` | `list[str]` | Ids des entités concernées : site, bâtiment ou système |
| `energy_type` | sourcé `EnergyType` | Pour un compteur d'énergie |
| `usage` | sourcé `EndUse` | Usage mesuré |
| `timestep` | sourcé `str` | Pas de temps nominal (`"10min"`, `"1h"`, `"1D"`…) |
| `channels` | `list[Channel]` | Grandeurs mesurées (une colonne par grandeur) |
| `data_source` | `DataSource \| None` | Où lire les données (nommé ainsi pour ne pas confondre avec la `source` de provenance) |
| `original_data_source` | `DataSource \| None` | Source d'origine, conservée après `save(include_data=True)` |
| `tags` | `dict[str, str]` | Surcharge des tags tide (`bloc`, `sub_bloc`) |
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
                     usage="total", timestep="30min",
                     channels=[Channel(name="power", unit="W", column="valeur")],
                     data_source=CsvSource(path="data/enedis_PDL_123.csv", index_col="horodate")))

park.add_meter(Meter(id="SUB_chauff", scope=["heating-chaufferie-A"],
                     energy_type="natural_gas", usage="heating", timestep="1D",
                     channels=[Channel(name="energy", unit="kWh")]))
```

### 6.2 Sources de données

```python
class DataSource(BaseModel):          # base abstraite, discriminée par `type`
    type: str
    def load(self) -> pd.DataFrame: ...

class CsvSource(DataSource):     type = "csv";     path; index_col; sep; decimal; tz; tz_ambiguous; ...
class ParquetSource(DataSource): type = "parquet"; path
```

- Un projet crée sa propre source en sous-classant `DataSource`, par exemple pour une API client.
  L'enregistrement est automatique dès que la classe est importée (registre par `type`, pour le chargement
  JSON).
- Si le JSON référence une source dont le `type` n'est pas enregistré, la description est conservée telle
  quelle, et `load()` lève une erreur explicite.

### 6.3 Chargement et données en mémoire

```python
m.load()                 # data_source.load() -> sélection et renommage des colonnes -> m.data
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
| `sub_bloc` | `usage` (ex. `heating`) |

`meter.tags` permet de surcharger `bloc` et `sub_bloc`. Exemple : `PDL_123_power__W__site-ecole-A__total`.

`park.timeseries(...)` assemble un DataFrame unique à partir de plusieurs compteurs, directement utilisable
par tide :

```python
df = park.timeseries(usage="heating", buildings=[...], load=True)  # filtres optionnels
from tide.plumbing import Plumber
Plumber(df).show()
```

- Les index sont convertis dans un fuseau unique (`tz="UTC"` par défaut, paramétrable), puis unis
  (outer join), sans rééchantillonnage : c'est le rôle de tide.
- `load=True` appelle `load()` sur les compteurs dont `data` est vide.
- Filtres : `usage`, `energy_type`, `meters` (ids), `buildings` / `sites` (ids ou objets). Un filtre sur
  des bâtiments retient les compteurs dont le `scope` contient ces bâtiments ou leurs systèmes propres.
- Un doublon de nom de colonne lève une erreur.

---

## 7. Persistance

### 7.1 API

```python
park.to_json() -> str
BuildingPark.from_json(text, site_cls=..., building_cls=...)

park.save("bayonne/")                     # bayonne/park.json
park.save("bayonne/", include_data=True)  # + bayonne/timeseries/<meter_id>.parquet
BuildingPark.load("bayonne/", site_cls=..., building_cls=...)

BuildingPark.json_schema(site_cls=..., building_cls=...) -> dict
```

Avec `include_data=True`, chaque Meter qui a des données en mémoire les écrit dans
`timeseries/<meter_id>.parquet`. Sa `data_source` devient alors une `ParquetSource` vers ce fichier
(chemin relatif au dossier du parc). L'ancienne est conservée dans `meter.original_data_source` pour la
traçabilité. Le parc sauvegardé est alors **autonome**.

### 7.2 Format JSON (extrait)

```json
{
  "bstock_version": "0.1.0",
  "schema_version": 1,
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
        "year_construction": [
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
    "data_source": {"type": "parquet", "path": "timeseries/PDL_123.parquet"}
  }]
}
```

Seuls les candidats sont sérialisés, pas la valeur effective : elle est recalculée au chargement.

---

## 8. Exemple de bout en bout (v1)

```python
from datetime import date
from bstock import BuildingPark, HeatingGenerator, Meter, Channel, CsvSource
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
                 owner=b.site)

park.add_meter(Meter(id="PDL_123", scope=[b.site.id], usage="total", timestep="30min",
                     energy_type="electricity",
                     channels=[Channel(name="power", unit="W", column="valeur")],
                     data_source=CsvSource(path="data/enedis_PDL_123.csv", index_col="horodate")))

df = park.timeseries(load=True)
park.save("out/bayonne/", include_data=True)

# plus tard, ailleurs
park2 = BuildingPark.load("out/bayonne/", site_cls=BayonneSite, building_cls=BayonneBuilding)
park2.get("A1B2C3D4E5F6").sources("heated_area")
```

---

## 9. Organisation du package (proposition)

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
  sources.py           # DataSource, CsvSource, ParquetSource, registre
  io.py                # to_json/from_json, save/load, JSON Schema
  exceptions.py        # BstockError et exceptions dérivées
tests/
```

- Python ≥ 3.11.
- Dépendances : `pydantic>=2`, `pandas`, `pyarrow` (Parquet). Extras : `[plot]` (plotly, seule lib de
  graphiques), `[dev]` (pytest, ruff). tide n'est **pas** une dépendance : bstock
  respecte seulement sa convention de nommage.
- Outillage et conventions : voir `CLAUDE.md`. Plan de réalisation : `PLAN.md`.

---

## 10. Feuille de route

| Version | Contenu |
|---|---|
| **v1** | Modèle (Park, Site, Building, Envelope, systèmes, Meter), provenance multi-sources, extension par héritage, sources CSV/Parquet, `timeseries()` au format tide, save/load JSON + Parquet, JSON Schema |
| v2 | Connecteurs API RNB, BDNB, DPE (création et enrichissement via `new_building` / `set(source=...)` ; un groupe BDNB à plusieurs bâtiments RNB devient un sous-site, un groupe à un seul bâtiment est porté par le bâtiment) ; exports `to_dataframe()`, `to_geodataframe()`, GeoPackage, GeoParquet ; emprise au sol (`footprint`) |
| v3 | Filtrage (`park.buildings.filter(...)`), agrégations par site ou par parc (grandeurs extensives), rapport de complétude et de provenance |

---

## 11. Questions ouvertes

1. Bâtiment client qui couvre **plusieurs ID RNB** : il se modélise comme un site contenant ces bâtiments.
   À valider à l'usage.

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

À noter : dans l'API ADEME, la colonne s'appelle `qualite_isolation_plancher bas` (avec une espace).

### A.5 GeneratorTechnology (principes)

- **BDNB** (`type_generateur_chauffage`, `_ecs`, `_climatisation`) : libellé = technologie + énergie, par
  exemple `chaudiere gaz condensation` → `BOILER_CONDENSING` + `NATURAL_GAS`, `pac air/eau` →
  `HEAT_PUMP_AIR_WATER`, `generateurs a effet joule` → `ELECTRIC_RESISTANCE`, `reseau de chaleur` →
  `DISTRICT_SUBSTATION` + `DISTRICT_HEATING`, `ecs thermodynamique electrique(pac ou ballon)` →
  `THERMODYNAMIC_WATER_HEATER`.
- **DPE** : 171 types de chauffage et 134 types d'ECS. On s'appuie sur la colonne `categorie_open_data`
  des tables d'enums, puis sur le libellé, qui contient aussi la période d'installation (« Chaudière gaz à
  condensation après 2015 »).
- La table de correspondance complète sera écrite avec les connecteurs (v2).
