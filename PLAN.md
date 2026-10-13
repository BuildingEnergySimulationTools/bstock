# PLAN — réalisation de bstock

Règles (voir `CLAUDE.md`) :
- chaque étape se termine par `ruff check`, `ruff format` et `pytest` au vert, puis un commit ;
- une case n'est cochée que si le code **et** ses tests sont faits ;
- un écart avec la SPEC passe d'abord par une mise à jour de `SPEC.md`.

Sommaire : [v1](#v1--terminée) (terminée, 0.1.0 puis ajustements d'API en 0.2.0) ·
[v2](#v2--connecteurs-emprise-et-exports) (en cours) · [v3](#v3--rappel-de-la-feuille-de-route)

---

# v1 — terminée

Périmètre v1 (SPEC §10) : modèle, provenance multi-sources et héritage, extension par projet, compteurs
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
- [x] Exemple de bout en bout (SPEC §8) : `examples/bayonne.py`, exécuté par `tests/test_examples.py`
- [x] Revue de l'API publique (`__all__`) et des docstrings NumPy
- [x] README complété (installation, exemple court)
- [x] Mise à jour de `SPEC.md` si l'implémentation a fait évoluer des détails
- [x] Version `0.1.0` et tag git

---

# v2 — connecteurs, emprise et exports

Périmètre v2 (SPEC §10) : connecteurs API RNB, BDNB et DPE, emprise au sol (`footprint`), exports
`to_dataframe()`, `to_geodataframe()`, GeoPackage et GeoParquet. Version cible : **0.3.0**.

## Décisions de conception (interview du 2026-10-13)

À reporter dans `SPEC.md` à l'étape 11 avant tout code.

| # | Sujet | Décision |
|---|---|---|
| D1 | Client HTTP | `requests`, dans un extra `[connectors]`, importé dans les fonctions qui en ont besoin (message clair s'il manque) |
| D2 | Géométrie | `footprint` = dict GeoJSON (`Polygon` / `MultiPolygon`) en **WGS84**, dans le socle, sans dépendance. shapely, geopandas et pyproj dans un extra `[geo]` : exports géographiques et reprojection des géométries BDNB (Lambert-93, EPSG:2154) |
| D3 | API des connecteurs | En couches, pour chaque source : **client** brut (`RnbClient`, `BdnbClient`, `DpeClient` ; URL de base, session et clé d'API configurables), **correspondances** pures (réponse → candidats, avec `raw` et `ref`), **fonctions de haut niveau** sur le parc (`rnb.add_buildings`, `rnb.enrich`, `bdnb.enrich`, `dpe.enrich`) qui acceptent un `client=` injectable |
| D4 | Groupe BDNB | Groupe à plusieurs bâtiments → sous-site `bdnb-bg-…` créé sous le parent commun, bâtiments déplacés (`group_sites=True` par défaut, désactivable). Bâtiments du groupe dans des parents différents : pas de restructuration, avertissement |
| D5 | Jeux DPE | `dpe03existant`, `dpe02neuf` (même schéma) et `dpe01tertiaire`. XML DPE hors v2 |
| D6 | Sélection des DPE | Seuls les DPE à l'échelle du bâtiment (immeuble, maison, tertiaire) alimentent les attributs ; le plus récent l'emporte (`date` = date d'établissement, `ref` = n° DPE). Les DPE d'appartement sont seulement ajoutés à `dpe_ids` |
| D7 | Groupe BDNB à un seul bâtiment du parc | On compte les constructions **dans la BDNB**, pas dans le parc : un groupe de plusieurs constructions devient un sous-site même s'il ne contient qu'un bâtiment du parc (intensifs hérités, extensifs du groupe sur le site, sans double compte). Valeurs portées par le bâtiment seulement si le groupe n'a qu'une construction |
| D8 | Surface DPE | Surface habitable (DPE logement) et surface utile (DPE tertiaire) → `heated_area`, avec un `comment` qui nomme la surface d'origine. `floor_area` (surface de plancher) n'est jamais alimentée par le DPE |
| D9 | `footprint_area` calculée | `compute_footprint_area()` ajoute un candidat `"computed"` à partir de `footprint`, sans dépendance : projection équirectangulaire locale autour du centroïde puis formule du lacet (écart < 0,1 % à l'échelle d'un bâtiment). La valeur BDNB (`s_geom_cstr`) reste un candidat `"bdnb"` |
| D10 | Bâtiments sans `rnb_id` | `rnb.suggest_matches(park, ...)` propose des candidats RNB par point (`lat`/`lon`) ou par adresse, dans un DataFrame (distance, adresse, emprise) validé par l'utilisateur. Aucune affectation automatique, sauf `assign="unique"` (un seul candidat dans le rayon) |

Principes communs aux connecteurs (proposés, à valider avec la SPEC) :
- noms de source `"rnb"`, `"bdnb"` et `"dpe"`, déjà présents dans la priorité par défaut ;
- **idempotence** : relancer un enrichissement remplace les candidats de la même source (un candidat par
  source, §4.2) et met à jour les systèmes déjà créés au lieu de les dupliquer. Les systèmes créés par
  un connecteur ont donc un id déterministe (ex. `heating-<owner_id>-bdnb-1`) ;
- un libellé source inconnu des tables de correspondance n'arrête pas l'import : la valeur prend `OTHER`
  quand l'enum l'a, sinon elle n'est pas posée, et un `UnmappedValueWarning` cite le libellé ;
- les fonctions de haut niveau renvoient un **rapport** (entités créées, enrichies, non trouvées,
  ambiguës) plutôt que de tout signaler par des avertissements ;
- pas de cache dans bstock : l'utilisateur sauvegarde le parc enrichi, ou passe sa propre session
  (`requests-cache` par exemple) au client ;
- le format JSON ne change pas (`schema_version` reste 1) : `footprint` est un attribut sourcé comme les
  autres.

## Étape 11 — SPEC v2
- [ ] Reporter D1 à D10 et les principes communs dans `SPEC.md` : nouvelle section « Connecteurs »,
      `footprint` et `compute_footprint_area()` au §3.6, exports au §7 ou dans une section dédiée,
      extras au §9 ; retirer de §10 la règle « groupe à un seul bâtiment » au profit de D7
- [ ] Vérifier les contrats d'API actuels (endpoints, pagination, quotas, champs) et les noter en annexe A,
      en particulier la recherche RNB par point (bâtiment le plus proche) et par adresse pour D10 ;
      évaluer le besoin d'un géocodage BAN pour les adresses
- [ ] Compléter l'annexe A : tables BDNB et DPE des générateurs (A.5), correspondances des surfaces (D8)
      et de l'usage DPE

## Étape 12 — Emprise au sol (socle)
- [ ] Champ `footprint` (sourcé, dict GeoJSON, `inherit=False`) sur Site et Building
- [ ] Validation légère sans dépendance : type `Polygon` / `MultiPolygon`, anneaux fermés,
      coordonnées dans les bornes WGS84 (erreur de validation sinon)
- [ ] `compute_footprint_area()` sur Site et Building (D9) : candidat `"computed"`, trous et
      `MultiPolygon` pris en compte, `IncompleteDataError` sans emprise
- [ ] Tests : validation, aller-retour JSON, non hérité, JSON Schema, aire calculée comparée à une
      valeur de référence (écart < 0,1 %), priorité `computed` vs `bdnb`

## Étape 13 — Export tabulaire
- [ ] `park.to_dataframe(kind="building" | "site")` : une ligne par entité, colonnes = id, `rnb_id`,
      ids BDNB, id du parent, valeurs effectives des attributs sourcés (héritage compris, y compris les
      champs ajoutés par une sous-classe projet)
- [ ] Option de provenance : colonnes `<attr>_source` (et `<attr>_provider` pour une valeur héritée)
- [ ] Types : enums en chaînes, listes conservées, `footprint` exclu par défaut
- [ ] Tests : sous-classe projet, héritage, provenance, parc vide

## Étape 14 — Socle des connecteurs
- [ ] Extras `[connectors]` (`requests>=2`) et `[geo]` (`shapely>=2`, `geopandas>=1`, `pyproj>=3`) dans
      `pyproject.toml`
- [ ] Paquet `bstock.connectors` : `_http.py` (session, timeout, nouvel essai sur 429/5xx avec attente,
      pagination, en-tête User-Agent), import paresseux de `requests`
- [ ] Exceptions et avertissements : `ConnectorError` (erreur HTTP ou réponse inattendue),
      `MissingDependencyError` (extra absent), `UnmappedValueWarning`
- [ ] Rapport d'import (`ImportReport` : créés, enrichis, non trouvés, ambigus)
- [ ] Convention de test : réponses réelles enregistrées dans `tests/data/{rnb,bdnb,dpe}/`, un faux
      client injecté pour les tests unitaires, tests `@pytest.mark.network` qui vérifient le contrat
- [ ] Tests : retry, pagination, erreurs HTTP, dépendance manquante (session factice, sans réseau)

## Étape 15 — Tables de correspondance (annexe A)
- [ ] Module interne `connectors/_mappings.py` : fonctions pures libellé → enum, avec normalisation
      (casse, accents, tiret demi-cadratin, apostrophe typographique)
- [ ] `BuildingUsage` (A.2), `EnergyType` (A.3), enveloppe DPE (A.4)
- [ ] Générateurs : libellés BDNB → `technology` + `energy_type` ; types DPE via `categorie_open_data`
      puis libellé (A.5)
- [ ] Tests paramétrés sur chaque ligne des tables, libellé inconnu → `OTHER` ou rien, avec
      `UnmappedValueWarning`

## Étape 16 — Connecteur RNB
- [ ] `RnbClient` : bâtiment par `rnb_id`, liste par ids (et par emprise/commune si utile à
      `add_buildings`)
- [ ] Correspondances : `point` → `lat`/`lon`, `shape` → `footprint`, adresses → `address`,
      `municipality`, `insee_code` ; `ext_ids` BDNB → `bdnb_batiment_construction_id`
- [ ] `rnb.add_buildings(park, rnb_ids, parent=None)` : crée les bâtiments absents via
      `park.new_building` (donc avec `building_cls`), enrichit les existants
- [ ] `rnb.enrich(park, buildings=None)` : bâtiments du parc qui ont un `rnb_id`
- [ ] Bâtiment démoli ou id inconnu du RNB : signalé dans le rapport, rien n'est supprimé
- [ ] `rnb.suggest_matches(park, buildings=None, radius=..., assign=None)` (D10) : candidats par point
      et/ou par adresse → DataFrame (id du bâtiment, `rnb_id` candidat, distance, adresse RNB) ;
      `assign="unique"` pose le `rnb_id` (source de la décision tracée dans le rapport) quand il n'y a
      qu'un candidat ; `rnb_id` déjà pris dans le parc → candidat signalé, jamais affecté
- [ ] Tests : correspondances sur réponses enregistrées, classe projet respectée, idempotence,
      suggestions (zéro, un, plusieurs candidats ; `rnb_id` déjà utilisé) ; un test `network`

## Étape 17 — Connecteur BDNB
- [ ] `BdnbClient` (clé d'API optionnelle) : construction par `rnb_id`, groupe par
      `batiment_groupe_id`, constructions d'un groupe
- [ ] Correspondances construction → bâtiment : `bdnb_batiment_construction_id`, `height`,
      `footprint_area` (`s_geom_cstr`), `footprint` (reprojeté, extra `[geo]` ; sans l'extra, ignoré
      avec un avertissement)
- [ ] Correspondances groupe : `usage`, `construction_year`, `dwelling_count`, `level_count`,
      emprise du groupe, générateurs de chauffage, d'ECS et de froid (systèmes à id déterministe)
- [ ] Règle des groupes (D4, D7) : groupe de plusieurs constructions BDNB → sous-site `bdnb-bg-…` sous
      le parent commun avec `park.move`, même avec un seul bâtiment du parc ; groupe d'une seule
      construction → valeurs portées par le bâtiment ; `group_sites=False` pour désactiver ;
      avertissement si les bâtiments ont des parents différents ; sous-site existant réutilisé
- [ ] `bdnb.enrich(park, buildings=None, group_sites=True)`
- [ ] Tests : groupe à un et à plusieurs bâtiments, parents différents, relance idempotente, héritage
      des valeurs de groupe, systèmes de site ; un test `network`

## Étape 18 — Connecteur DPE
- [ ] `DpeClient` (API data-fair de l'ADEME) : DPE par numéro, par `id_rnb`, sur les trois jeux (D5)
- [ ] Sélection (D6) : DPE à l'échelle du bâtiment seulement, le plus récent ; DPE d'appartement
      ajoutés à `dpe_ids` sans poser d'attributs
- [ ] Correspondances logement : surfaces, `construction_year` / période, `u_bat`, inertie, qualité
      d'isolation, un `EnvelopeComponent` par poste avec `heat_loss_coefficient`, générateurs
      (`technology`, `energy_type`, ratios, `storage_volume`)
- [ ] Surfaces (D8) : surface habitable ou utile → `heated_area`, `comment` = nom de la surface d'origine
- [ ] Correspondances tertiaire : surface utile, usage, énergie
- [ ] `dpe.enrich(park, buildings=None)` : par `rnb_id` et par `dpe_ids` déjà connus
- [ ] Tests : sélection (appartement ignoré, plus récent retenu), correspondances sur réponses
      enregistrées, idempotence des parois et systèmes ; un test `network` par jeu

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
