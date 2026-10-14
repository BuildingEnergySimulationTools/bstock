# PLAN — réalisation de bstock

Règles (voir `CLAUDE.md`) :
- chaque étape se termine par `ruff check`, `ruff format` et `pytest` au vert, puis un commit ;
- une case n'est cochée que si le code **et** ses tests sont faits ;
- un écart avec la SPEC passe d'abord par une mise à jour de `SPEC.md`.

Sommaire : [v1](#v1--terminée) (terminée, 0.1.0 puis ajustements d'API en 0.2.0) ·
[v2](#v2--connecteurs-emprise-et-exports) (en cours) · [v3](#v3--rappel-de-la-feuille-de-route)

---

# v1 — terminée

Périmètre v1 (SPEC §12) : modèle, provenance multi-sources et héritage, extension par projet, compteurs
et sources CSV/Parquet, `timeseries()` au format tide, persistance JSON + Parquet.

---

## Étape 0 — Squelette du projet
- [x] `pyproject.toml` : métadonnées, `requires-python = ">=3.11"`, dépendances (`pydantic>=2`, `pandas`,
      `pyarrow`), extras `[plot]` (plotly) et `[dev]` (pytest, ruff)
- [x] Configuration ruff (règles, longueur de ligne, `target-version = "py311"`) et pytest (marqueur
      `network`, `testpaths`)
- [x] Arborescence `src/bstock/` + `tests/`, `__init__.py` avec `__version__` et `__all__`
- [x] `exceptions.py` : `BstockError`, `DuplicateIdError`, `UnknownIdError`, `EntityInUseError`…
- [x] `README.md` minimal (objectif, installation, lien vers la SPEC)
- [x] Installation éditable dans le `.venv` ; `pytest` et `ruff` tournent sur un test trivial

## Étape 1 — Enums (§3.9)
- [x] `enums.py` : `BuildingUsage`, `EnergyType`, `GeneratorTechnology`, `Inertia`, `InsulationLevel`,
      `GlazingType`, `FrameType`, `ComponentKind`, `Orientation`, `Adjacency`, `EndUse` (`StrEnum`,
      valeur = nom en minuscules)
- [x] Tests : valeurs, sérialisation, acceptation de la chaîne ou du membre

## Étape 2 — Cœur de la provenance (§4.1 à §4.3, §4.5)
- [x] `SourcedValue` (`value`, `source`, `date`, `comment`, `ref`, `raw`)
- [x] Helper `sourced(unit=, inherit=, description=, **contraintes)` et métadonnées de champ
      (`sourced_fields()`)
- [x] `SourcedModel` : stockage des candidats, propriétés qui renvoient la valeur effective
- [x] API `set` / `unset` / `sources` / `effective` ; affectation directe = source `"manual"` ;
      affectation de `None` = retrait du candidat `"manual"`
- [x] Validation des valeurs contre le type et les contraintes du champ (`TypeAdapter`)
- [x] Construction avec kwargs et paramètre `source=`
- [x] `SourcePriority` : liste par défaut, surcharge par attribut, joker `"*"`, départage par date puis
      ordre d'ajout
- [x] Tests : résolution (cas nominal, joker, sources inconnues, égalités), un candidat par source,
      validation en erreur, champs ajoutés dans une sous-classe

## Étape 3 — Héritage des valeurs (§4.4)
- [x] Lien parent (non sérialisé) sur `SourcedModel` et résolution en remontant la chaîne
- [x] `origin(attr)` et `sources(attr, inherited=True)`
- [x] Champs `inherit=False` jamais hérités
- [x] Tests : niveau le plus proche prioritaire, sites imbriqués sur plusieurs niveaux, extensif non
      hérité, entité sans parent

## Étape 4 — Enveloppe (§3.7)
- [x] `Envelope` (synthèse + `components`), `EnvelopeComponent`, `Window`
- [x] Héritage de l'enveloppe d'un bâtiment depuis celle de son site
- [x] Calculs `compute_heat_loss_coefficient`, `compute_loss_area`, `compute_u_bat`,
      `compute_window_shgc` (candidats `source="computed"`) ; filtre `components_by`
- [x] Tests : calculs (H fourni ou U·A, ponts thermiques), priorité `computed` vs `audit`, héritage

## Étape 5 — Systèmes (§3.8)
- [x] `System`, `Generator`, `HeatingGenerator`, `CoolingGenerator`, `DHWGenerator`
      (`storage_volume`), `Ventilation`, `SolarPV`
- [x] Avertissements de cohérence des ratios de performance selon `technology`
- [x] Tests : construction, avertissements (chaudière > 1,1 ; PAC < 1), unités déclarées

## Étape 6 — Site et Building (§3.5, §3.6)
- [x] Attributs descriptifs communs (`description.py`) avec le drapeau d'héritage de chaque champ
- [x] `Site` (sous-sites, bâtiments, systèmes partagés) et `Building` (identifiants, systèmes propres)
- [x] Validation légère des formats RNB, BDNB et DPE (avertissement)
- [x] Tests : construction, héritage site → bâtiment sur les attributs du §3.6, formats d'identifiants

## Étape 7 — BuildingPark (§3.2, §3.4, §5)
- [x] Index des ids, unicité (y compris `rnb_id`), interdiction de `__`, génération des ids
- [x] `new_site`, `new_building`, `add_site`, `add_building`, `add_system`, `get`, `get_by_rnb`
- [x] `iter_sites`, `iter_buildings`, `iter_systems` ; accès dérivés `building.site`, `.park`,
      `site.parent`, `iter_*` récursifs
- [x] `move` et `remove` (contrôle des références, `force=True`)
- [x] `site_cls` / `building_cls` utilisés par `new_*`, contrôle de classe dans `add_*`
- [x] `building.systems(kind, include_shared=True)` avec `serves` (validation : bâtiments du sous-arbre)
- [x] Priorité de sources portée par le parc et utilisée par toutes les entités rattachées
- [x] Tests : cas d'erreur (id dupliqué, id inconnu, mauvaise classe), sous-classe projet type
      `BayonneBuilding`, systèmes partagés, déplacement et suppression

## Étape 8 — Compteurs et sources de données (§6)
- [x] `Channel`, `Meter` (scope validé par le parc), `park.add_meter`, `building.meters()`
- [x] `DataSource` avec registre par `type` ; `CsvSource` (`tz`, `tz_ambiguous`…), `ParquetSource` ;
      conservation d'une source de type inconnu
- [x] `meter.load()` et le setter de `meter.data` : DatetimeIndex trié, fuseau obligatoire, colonnes
      renommées au format tide
- [x] Génération des noms `name__unit__bloc__sub_bloc` et surcharge par `meter.tags`
- [x] `park.timeseries(...)` : filtres, conversion de fuseau, outer join, erreur si doublon de colonne
- [x] Tests : index sans fuseau refusé ou localisé, changement d'heure, noms tide, assemblage multi-compteurs

## Étape 9 — Persistance (§7)
- [x] `to_json` / `from_json` : arbre complet, candidats seulement, métadonnées de version et de classes
- [x] Conservation des attributs inconnus avec avertissement ; avertissement si les classes diffèrent
- [x] `save(dir)` / `load(dir)` ; `include_data=True` → Parquet par compteur, `source` repointée,
      `original_source` conservée
- [x] `json_schema(site_cls=, building_cls=)`
- [x] Tests : aller-retour (sites imbriqués, sous-classes, provenance, héritage), attributs inconnus
      préservés, parc autonome rechargé sans les fichiers d'origine (`tmp_path`)

## Étape 10 — Finalisation v0.1.0
- [x] Exemple de bout en bout (SPEC §10) : `examples/bayonne.py`, exécuté par `tests/test_examples.py`
- [x] Revue de l'API publique (`__all__`) et des docstrings NumPy
- [x] README complété (installation, exemple court)
- [x] Mise à jour de `SPEC.md` si l'implémentation a fait évoluer des détails
- [x] Version `0.1.0` et tag git

---

# v2 — connecteurs, emprise et exports

Périmètre v2 (SPEC §12) : connecteurs API RNB, BDNB et DPE, emprise au sol (`footprint`), exports
`to_dataframe()`, `to_geodataframe()`, GeoPackage et GeoParquet. Version cible : **0.3.0**.

## Décisions de conception (interviews du 2026-10-13)

Reportées dans `SPEC.md` (§3.6, §8, §9, annexe A) à l'étape 11.

| # | Sujet | Décision |
|---|---|---|
| D1 | Client HTTP | `requests`, dans un extra `[connectors]`, importé dans les fonctions qui en ont besoin (message clair s'il manque) |
| D2 | Géométrie | `footprint` = dict GeoJSON (`Polygon` / `MultiPolygon`) en **WGS84**, dans le socle, sans dépendance. shapely, geopandas et pyproj dans un extra `[geo]` : exports géographiques et reprojection des géométries BDNB (Lambert-93, EPSG:2154) |
| D3 | API des connecteurs | En couches, pour chaque source : **client** brut (`RnbClient`, `BdnbClient`, `DpeClient` ; URL de base, session et clé d'API configurables), **correspondances** pures (réponse → candidats, avec `raw` et `ref`), **fonctions de haut niveau** sur le parc (`rnb.add_buildings`, `rnb.enrich`, `bdnb.enrich`, `dpe.enrich`) qui acceptent un `client=` injectable |
| D4 | Groupe BDNB | Groupe à plusieurs bâtiments → sous-site `bdnb-bg-…` créé sous le parent commun, bâtiments déplacés (`group_sites=True` par défaut, désactivable). Bâtiments du groupe dans des parents différents : pas de restructuration, signalé dans le rapport |
| D5 | Jeux DPE | `dpe03existant`, `dpe02neuf` (même schéma) et `dpe01tertiaire`. XML DPE hors v2 |
| D6 | Sélection des DPE | Seuls les DPE à l'échelle du bâtiment (immeuble, maison, tertiaire) alimentent les attributs ; le plus récent l'emporte (`date` = date d'établissement, `ref` = n° DPE). Les DPE d'appartement sont seulement ajoutés à `dpe_ids` |
| D7 | Groupe BDNB à un seul bâtiment du parc | On compte les constructions **dans la BDNB**, pas dans le parc : un groupe de plusieurs constructions devient un sous-site même s'il ne contient qu'un bâtiment du parc (intensifs hérités, extensifs du groupe sur le site, sans double compte). Valeurs portées par le bâtiment seulement si le groupe n'a qu'une construction |
| D8 | Surface DPE | Surface habitable (DPE logement) et surface utile (DPE tertiaire) → `heated_area`, avec un `comment` qui nomme la surface d'origine. `floor_area` (surface de plancher) n'est jamais alimentée par le DPE |
| D9 | `footprint_area` calculée | `compute_footprint_area()` ajoute un candidat `"computed"` à partir de `footprint`, sans dépendance : projection équirectangulaire locale autour du centroïde puis formule du lacet (écart < 0,1 % à l'échelle d'un bâtiment). La valeur BDNB (`s_geom_cstr`) reste un candidat `"bdnb"` |
| D10 | Bâtiments sans `rnb_id` | `rnb.suggest_matches(park, ...)` propose des candidats RNB par point (`lat`/`lon`) ou par adresse, dans un DataFrame (distance, adresse, emprise) validé par l'utilisateur. Aucune affectation automatique, sauf `assign="unique"` (un seul candidat dans le rayon) |
| D11 | Technologies génériques | `GeneratorTechnology` reçoit `BOILER`, `HEAT_PUMP` et `WATER_HEATER` (type exact inconnu) pour les libellés imprécis des sources ; contrôle des ratios selon la famille |
| D12 | Libellés de générateurs DPE | Table de données `connectors/data/dpe_generators.csv` (libellé → `technology`, `energy_type`), générée une fois depuis les tables de l'Observatoire DPE (Licence Ouverte 2.0) par un script du dépôt, puis relue. Libellés BDNB : table écrite dans le code (SPEC A.5) |

Règles communes aux connecteurs : SPEC §8.2 (sources, idempotence et ids déterministes, libellés
inconnus, `ImportReport`, erreurs, pas de cache, format JSON inchangé).

## Étape 11 — SPEC v2
- [x] Reporter D1 à D12 et les règles communes dans `SPEC.md` : §8 Connecteurs, §9 Exports, `footprint`
      et `compute_footprint_area()` au §3.6, extras et organisation au §11, feuille de route au §12
- [x] Vérifier les contrats d'API (endpoints, pagination, quotas, champs) et les noter en annexe A.6 ;
      recherche RNB par point et par adresse (D10) : le RNB géocode lui-même avec la BAN, pas de
      géocodage séparé
- [x] Compléter l'annexe A : générateurs BDNB et principe de la table DPE (A.5), surfaces (D8), usage
      DPE (§8.5), clés exactes de l'API DPE (A.4)

## Étape 12 — Emprise au sol (socle)
- [x] Champ `footprint` (sourcé, dict GeoJSON, `inherit=False`) sur Site et Building
- [x] Validation légère sans dépendance : type `Polygon` / `MultiPolygon`, anneaux fermés d'au moins
      4 positions, coordonnées dans les bornes WGS84 (`ValidationError` sinon)
- [x] `compute_footprint_area()` sur Site et Building (D9) : candidat `"computed"`, trous et
      `MultiPolygon` pris en compte, `IncompleteDataError` sans emprise
- [x] Tests : validation, aller-retour JSON, non hérité, JSON Schema, aire calculée comparée à une
      valeur de référence (écart < 0,1 %), priorité `computed` vs `bdnb`

## Étape 13 — Export tabulaire
- [x] `park.to_dataframe(kind="building" | "site", provenance=False)` (module `export.py`) : une ligne
      par entité indexée par `id`, colonnes `rnb_id`, ids BDNB, `dpe_ids`, `parent_id`, valeurs
      effectives des attributs sourcés (héritage compris, y compris les champs d'une sous-classe projet)
- [x] `provenance=True` : colonnes `<attr>_source` et `<attr>_provider`
- [x] Types : enums en chaînes, listes conservées, `footprint` exclu
- [x] Tests : sous-classe projet, héritage, provenance, parc vide

## Étape 14 — Socle des connecteurs
- [x] Extras `[connectors]` (`requests>=2`) et `[geo]` (`shapely>=2`, `geopandas>=1`, `pyproj>=3`) dans
      `pyproject.toml`
- [x] Paquet `bstock.connectors` : `_http.py` (session, timeout, nouvel essai sur 429/5xx et sur le
      message de budget de l'ADEME, avec attente ; pagination par `next` ; User-Agent), import
      paresseux de `requests`
- [x] Exceptions et avertissements : `ConnectorError`, `MissingDependencyError`, `UnmappedValueWarning`
- [x] `ImportReport` (créés, enrichis, non trouvés, ambigus, messages ; `to_dataframe()`)
- [x] Helper interne de reprojection Lambert-93 → WGS84 (extra `[geo]`)
- [x] Helper interne de systèmes et parois à id déterministe : création, mise à jour, retrait de ceux
      qui ne sont plus produits (conservés et signalés s'ils sont référencés)
- [x] Convention de test : réponses réelles enregistrées dans `tests/data/{rnb,bdnb,dpe}/` (reprises
      des réponses collectées à l'étape 11), faux client injecté, tests `@pytest.mark.network`
- [x] Tests : retry, pagination, erreurs HTTP, dépendance manquante (session factice, sans réseau)

## Étape 15 — Enums et tables de correspondance (annexe A)
- [ ] `GeneratorTechnology` : `BOILER`, `HEAT_PUMP`, `WATER_HEATER` (D11), et leurs familles dans le
      contrôle des ratios (`systems.py`)
- [ ] Module interne `connectors/_mappings.py` : fonctions pures libellé → enum ; comparaison exacte
      des libellés (les sources mélangent apostrophes droites et typographiques, tiret demi-cadratin
      dans les libellés bois)
- [ ] `BuildingUsage` (A.2), `EnergyType` (A.3), enveloppe DPE (A.4)
- [ ] Générateurs BDNB (table A.5, dans le code)
- [ ] `scripts/build_dpe_generators.py` et `connectors/data/dpe_generators.csv` (D12) : génération depuis
      `enum_tables.xlsx`, relecture ligne par ligne, en-tête citant la source et sa date ; fichier
      inclus dans le paquet
- [ ] Tests paramétrés sur les tables, libellé inconnu → `OTHER` ou rien, avec `UnmappedValueWarning` ;
      tous les libellés DPE collectés à l'étape 11 sont couverts par la table

## Étape 16 — Connecteur RNB
- [ ] `RnbClient` : bâtiment par id (une requête par id, pas de lecture groupée), historique d'un id
      inactif, bâtiment le plus proche (`point=lat,lon`, rayon ≤ 1000 m), recherche par adresse
- [ ] Correspondances : `point` → `lat`/`lon`, `shape` → `footprint` (si polygone), première adresse →
      `address`, `municipality`, `insee_code` ; `ext_ids` BDNB → `bdnb_batiment_construction_id` s'il
      est vide ; normalisation des ids (majuscules, sans tirets)
- [ ] `rnb.add_buildings(park, rnb_ids, parent=None)` : crée les bâtiments absents via
      `park.new_building` (donc avec `building_cls`), enrichit les existants
- [ ] `rnb.enrich(park, buildings=None)` : bâtiments du parc qui ont un `rnb_id`
- [ ] Rapport : id inconnu, démoli, inactif (successeurs lus dans l'historique) ; rien n'est supprimé
- [ ] `rnb.suggest_matches(park, buildings=None, by="point" | "address", radius=..., assign=None)`
      (D10) : DataFrame (id du bâtiment, `rnb_id` candidat, distance ou score BAN, adresse RNB) ;
      `assign="unique"` ; `rnb_id` déjà pris dans le parc → signalé, jamais affecté
- [ ] Tests : correspondances sur réponses enregistrées, classe projet respectée, idempotence,
      ids inactifs, suggestions (zéro, un, plusieurs candidats ; `rnb_id` déjà utilisé) ; un test
      `network`

## Étape 17 — Connecteur BDNB
- [ ] `BdnbClient(api_key=None)` : requêtes groupées par lots (`in.(...)`, taille selon l'offre),
      construction par `rnb_id`, groupe complet, DPE représentatif, nombre de constructions d'un groupe
      (`Prefer: count=exact`)
- [ ] Correspondances construction → bâtiment : `bdnb_batiment_construction_id`, `height`,
      `footprint_area` (`s_geom_cstr`), `footprint` (`geom_cstr` reprojetée, extra `[geo]` ; sans
      l'extra, ignorée avec un avertissement)
- [ ] Correspondances groupe : `usage`, `construction_year`, `dwelling_count`, `level_count`, `height`,
      emprise du groupe, générateurs principal et d'appoint (chauffage, ECS, froid) avec la date du DPE
      représentatif, numéro du DPE représentatif ajouté à `dpe_ids`
- [ ] Règle des groupes (D4, D7) : groupe de plusieurs constructions BDNB → sous-site `bdnb-bg-…` sous
      le parent commun avec `park.move`, même avec un seul bâtiment du parc ; groupe d'une seule
      construction → valeurs portées par le bâtiment ; parents différents ou `group_sites=False` →
      intensifs sur chaque bâtiment, extensifs non posés, signalé ; sous-site existant réutilisé
- [ ] Plusieurs constructions pour un même `rnb_id` → ambigu dans le rapport, non enrichi
- [ ] `bdnb.enrich(park, buildings=None, group_sites=True)`
- [ ] Tests : groupe à une et à plusieurs constructions, parents différents, relance idempotente,
      héritage des valeurs de groupe, systèmes de site, sans l'extra `[geo]` ; un test `network`

## Étape 18 — Connecteur DPE
- [ ] `DpeClient` (data-fair ADEME) : DPE par numéros et par `id_rnb` (filtres `_in`), sur les trois
      jeux (D5) ; `select` et `count=false` pour ménager le budget
- [ ] Sélection (D6) : `type_batiment` `immeuble` / `maison`, tertiaire hors « dans un bâtiment de
      logement » ; DPE d'appartement → `dpe_ids` seulement, et suivi de `numero_dpe_immeuble_associe` ;
      DPE remplacés écartés ; le plus récent retenu
- [ ] Correspondances logement : `construction_year`, `dwelling_count`, `level_count`, `usage`,
      `u_bat`, inertie, une paroi par poste avec `heat_loss_coefficient` et qualité d'isolation (toiture
      : la moins bonne des trois), générateurs (jusqu'à 4 de chauffage, 2 d'ECS ; `storage_volume`,
      COP d'ECS)
- [ ] Surfaces (D8) : surface habitable ou utile → `heated_area`, `comment` = nom de la surface d'origine
- [ ] Correspondances tertiaire : surface utile, usage, énergie de chauffage
- [ ] Valeurs posées sur l'entité qui porte le DPE (bâtiment, ou site par ses `dpe_ids`)
- [ ] `dpe.enrich(park, buildings=None)`
- [ ] Tests : sélection (appartement ignoré, immeuble associé suivi, remplacé écarté, plus récent
      retenu), correspondances sur réponses enregistrées, idempotence des parois et systèmes ; un test
      `network` par jeu

## Étape 19 — Exports géographiques (extra `[geo]`)
- [ ] `park.to_geodataframe(kind=..., geometry="footprint" | "point")` (CRS EPSG:4326 ; entités sans
      géométrie conservées avec une géométrie vide)
- [ ] `park.to_geopackage(path)` (une couche sites, une couche bâtiments) et `park.to_geoparquet(path)`
- [ ] Listes, enums et dicts convertis pour les formats qui ne les acceptent pas (JSON en chaîne)
- [ ] Tests (sautés si `[geo]` absent) : aller-retour de lecture GeoPackage et GeoParquet dans
      `tmp_path`, erreur claire sans l'extra

## Étape 20 — Finalisation v0.3.0
- [ ] Exemple de bout en bout `examples/connectors.py` : RNB → BDNB → DPE → export, exécuté dans les
      tests avec le faux client
- [ ] Revue de l'API publique (`__all__`, `bstock.connectors`) et des docstrings NumPy
- [ ] README (installation des extras, exemple court) ; statut de la SPEC passé en v2
- [ ] Version `0.3.0` et tag git

---

# v3 — rappel de la feuille de route
- Filtrage, agrégations des grandeurs extensives, rapport de complétude et de provenance ; graphiques
  plotly
