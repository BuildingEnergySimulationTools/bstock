# CLAUDE.md — bstock

Lib Python qui structure les données d'un parc de bâtiments (sites, bâtiments, systèmes, compteurs),
avec la provenance multi-sources des valeurs. **`SPEC.md` est la référence de conception** : la lire avant
toute évolution, et la mettre à jour quand un choix de conception change.

## Environnement

- Virtualenv local `.venv` (Python 3.14). Cible de compatibilité : **Python ≥ 3.11**. Ne pas utiliser de
  syntaxe plus récente que 3.11.
- Installation : `.venv/Scripts/python -m pip install -e ".[dev]"`
- Configuration unique dans `pyproject.toml` (build, dépendances, ruff, pytest). Pas de `setup.py` ni de
  `requirements.txt`.

## Commandes

```bash
.venv/Scripts/python -m pytest                 # tests
.venv/Scripts/python -m pytest -m "not network"  # sans les tests qui appellent des API
.venv/Scripts/python -m ruff check --fix .     # lint
.venv/Scripts/python -m ruff format .          # formatage
```

Avant de considérer une tâche terminée : `ruff check`, `ruff format` et `pytest` passent sans erreur.

## Organisation

```
src/bstock/        # code de la lib (src layout)
tests/             # miroir de src/bstock : tests/test_<module>.py
SPEC.md            # spécification
```

- L'API publique est exportée explicitement dans `src/bstock/__init__.py` (`__all__`). Tout le reste est
  considéré comme interne.
- Les modules privés ou les helpers internes sont préfixés par `_`.

## Style de code

- **Langue** : code, noms, docstrings et messages d'erreur en anglais. SPEC et échanges en français.
- **Typage complet** des signatures publiques (`X | None`, `list[str]`…), sans `Any` sauf nécessité.
- **Docstrings** au format NumPy pour toute fonction ou classe publique : description, `Parameters`,
  `Returns`, `Raises`, et unité des grandeurs physiques.
- **Pydantic v2** pour les modèles de données. Pas de dataclasses pour les entités du modèle.
- **Unités SI** fixées par champ et déclarées dans les métadonnées (`sourced(unit="m²")`). Jamais d'unité
  implicite.
- Pas de `print` dans la lib : `warnings.warn` pour les avertissements utilisateur, `logging`
  (`logging.getLogger(__name__)`) pour le diagnostic.
- Exceptions spécifiques à la lib dans `bstock/exceptions.py` (héritant d'une `BstockError`). Ne pas lever
  d'`Exception` générique.
- Préférer des fonctions courtes, sans état global. Pas de « magie » non documentée.

## Dépendances

- Socle minimal : `pydantic>=2`, `pandas`, `pyarrow`.
- **Graphiques : plotly uniquement** (pas de matplotlib). plotly est une dépendance optionnelle
  (`extra [plot]`), importée dans les fonctions qui en ont besoin. Les fonctions de tracé **renvoient une
  `plotly.graph_objects.Figure`** et ne l'affichent pas.
- Dev : `pytest`, `ruff` (`extra [dev]`).
- **Demander avant d'ajouter une dépendance**, et la placer dans un extra si elle n'est pas indispensable
  au socle.
- tide n'est pas une dépendance : bstock respecte seulement sa convention de colonnes
  `name__unit__bloc__sub_bloc`.

## Tests

- pytest, avec des fixtures dans `tests/conftest.py` (petit parc d'exemple réutilisable).
- Un test par comportement, nommé `test_<comportement>`. Utiliser `pytest.mark.parametrize` plutôt que des
  boucles.
- E/S dans `tmp_path`, jamais dans le dépôt.
- Les tests unitaires n'accèdent jamais au réseau. Les tests d'API réelles (RNB, BDNB, DPE) portent le
  marqueur `@pytest.mark.network`.
- Tout bug corrigé s'accompagne d'un test qui le reproduit.
- Priorités de couverture : résolution des priorités de sources, héritage site → bâtiment, aller-retour
  JSON (avec sous-classes et attributs inconnus), noms de colonnes tide, sauvegarde Parquet.

## Façon de travailler

- Respecter la SPEC. Si l'implémentation révèle un problème de conception, le signaler et proposer une
  modification de `SPEC.md` **avant** de s'en écarter.
- Ne pas modifier l'API publique (noms, signatures) sans le demander.
- Changements petits et ciblés ; pas de refactoring opportuniste hors de la tâche demandée.
