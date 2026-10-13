# PLAN — réalisation de bstock v1

Périmètre v1 (SPEC §10) : modèle, provenance multi-sources et héritage, extension par projet, compteurs
et sources CSV/Parquet, `timeseries()` au format tide, persistance JSON + Parquet.

Règles (voir `CLAUDE.md`) :
- chaque étape se termine par `ruff check`, `ruff format` et `pytest` au vert, puis un commit ;
- une case n'est cochée que si le code **et** ses tests sont faits ;
- un écart avec la SPEC passe d'abord par une mise à jour de `SPEC.md`.

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
- [ ] `System`, `Generator`, `HeatingGenerator`, `CoolingGenerator`, `DHWGenerator`
      (`storage_volume`), `Ventilation`, `SolarPV`
- [ ] Avertissements de cohérence des ratios de performance selon `technology`
- [ ] Tests : construction, avertissements (chaudière > 1,1 ; PAC < 1), unités déclarées

## Étape 6 — Site et Building (§3.5, §3.6)
- [ ] Attributs descriptifs communs (`description.py`) avec le drapeau d'héritage de chaque champ
- [ ] `Site` (sous-sites, bâtiments, systèmes partagés) et `Building` (identifiants, systèmes propres)
- [ ] Validation légère des formats RNB, BDNB et DPE (avertissement)
- [ ] Tests : construction, héritage site → bâtiment sur les attributs du §3.6, formats d'identifiants

## Étape 7 — BuildingPark (§3.2, §3.4, §5)
- [ ] Index des ids, unicité (y compris `rnb_id`), interdiction de `__`, génération des ids
- [ ] `new_site`, `new_building`, `add_site`, `add_building`, `add_system`, `get`, `get_by_rnb`
- [ ] `iter_sites`, `iter_buildings`, `iter_systems` ; accès dérivés `building.site`, `.park`,
      `site.parent`, `iter_*` récursifs
- [ ] `move` et `remove` (contrôle des références, `force=True`)
- [ ] `site_cls` / `building_cls` utilisés par `new_*`, contrôle de classe dans `add_*`
- [ ] `building.systems(kind, include_shared=True)` avec `serves` (validation : bâtiments du sous-arbre)
- [ ] Priorité de sources portée par le parc et utilisée par toutes les entités rattachées
- [ ] Tests : cas d'erreur (id dupliqué, id inconnu, mauvaise classe), sous-classe projet type
      `BayonneBuilding`, systèmes partagés, déplacement et suppression

## Étape 8 — Compteurs et sources de données (§6)
- [ ] `Channel`, `Meter` (scope validé par le parc), `park.add_meter`, `building.meters()`
- [ ] `DataSource` avec registre par `type` ; `CsvSource` (`tz`, `tz_ambiguous`…), `ParquetSource` ;
      conservation d'une source de type inconnu
- [ ] `meter.load()` et le setter de `meter.data` : DatetimeIndex trié, fuseau obligatoire, colonnes
      renommées au format tide
- [ ] Génération des noms `name__unit__bloc__sub_bloc` et surcharge par `meter.tags`
- [ ] `park.timeseries(...)` : filtres, conversion de fuseau, outer join, erreur si doublon de colonne
- [ ] Tests : index sans fuseau refusé ou localisé, changement d'heure, noms tide, assemblage multi-compteurs

## Étape 9 — Persistance (§7)
- [ ] `to_json` / `from_json` : arbre complet, candidats seulement, métadonnées de version et de classes
- [ ] Conservation des attributs inconnus avec avertissement ; avertissement si les classes diffèrent
- [ ] `save(dir)` / `load(dir)` ; `include_data=True` → Parquet par compteur, `source` repointée,
      `original_source` conservée
- [ ] `json_schema(site_cls=, building_cls=)`
- [ ] Tests : aller-retour (sites imbriqués, sous-classes, provenance, héritage), attributs inconnus
      préservés, parc autonome rechargé sans les fichiers d'origine (`tmp_path`)

## Étape 10 — Finalisation v0.1.0
- [ ] Exemple de bout en bout (SPEC §8) sous forme de script `examples/` exécuté par un test
- [ ] Revue de l'API publique (`__all__`) et des docstrings NumPy
- [ ] README complété (installation, exemple court)
- [ ] Mise à jour de `SPEC.md` si l'implémentation a fait évoluer des détails
- [ ] Version `0.1.0` et tag git

---

## Après la v1 (rappel de la feuille de route)
- v2 : connecteurs RNB, BDNB, DPE (tables de correspondance de l'annexe A) ; exports pandas, GeoPandas,
  GeoPackage, GeoParquet ; emprise au sol
- v3 : filtrage, agrégations des grandeurs extensives, rapport de complétude et de provenance ; graphiques
  plotly
