# CLAUDE.md — bstock

Lib Python qui structure les données d'un parc de bâtiments (sites, bâtiments, systèmes, compteurs),
avec la provenance multi-sources des valeurs. **`SPEC.md` est la référence de conception** : la lire avant
toute évolution, et la mettre à jour quand un choix de conception change.

## Environnement

- Virtualenv local `.venv` (Python 3.14). Cible de compatibilité : **Python ≥ 3.11**. Ne pas utiliser de
  syntaxe plus récente que 3.11.
- Installation : `.venv/Scripts/python -m pip install -e ".[dev,connectors,geo,plot]"` (les tests des connecteurs ont besoin des extras ; ceux des graphiques sont sautés sans plotly)
- Configuration unique dans `pyproject.toml` (build, dépendances, ruff, pytest). Pas de `setup.py` ni de
  `requirements.txt`.

## Commandes

```bash
.venv/Scripts/python -m pytest                 # tests (sans les tests qui appellent des API)
.venv/Scripts/python -m pytest -m network      # seulement les tests qui appellent des API
.venv/Scripts/python -m ruff check --fix .     # lint
.venv/Scripts/python -m ruff format .          # formatage
```

Avant de considérer une tâche terminée : `ruff check`, `ruff format` et `pytest` passent sans erreur.

## Organisation

```
src/bstock/        # code de la lib (src layout)
tests/             # miroir de src/bstock : tests/test_<module>.py
examples/          # exemples de bout en bout, chacun en deux versions
tutorial/          # notebook tutoriel (en français), versionné avec ses sorties (figures en HTML + PNG)
SPEC.md            # spécification
```

- **Exemples** : chaque exemple existe en deux versions maintenues en parallèle : `examples/<nom>.py`
  (script, fonction `main(output)`, sorties par `print`) et `examples/<nom>_interactive.py` (cellules
  `# %%` qui finissent par une expression affichée, objets laissés dans l'espace de noms). Toute
  modification de l'une est reportée dans l'autre (mêmes étapes, mêmes données). `tests/test_examples.py`
  exécute les deux et vérifie qu'elles produisent le même parc. Quand une fonction de la lib remplace
  un helper d'exemple, utiliser la fonction de la lib dans les deux versions.
- L'API publique est exportée explicitement dans `src/bstock/__init__.py` (`__all__`), plus les `__all__`
  des sous-modules documentés : `bstock.io`, `bstock.export`, `bstock.connectors` et ses modules `rnb`,
  `bdnb`, `dpe` (SPEC §11). Les `__all__` des autres modules ne listent que des noms réexportés par
  `bstock`. Tout le reste est considéré comme interne.
- Les modules privés ou les helpers internes sont préfixés par `_`.

## Style de code

- **Langue** : code, noms, docstrings et messages d'erreur en anglais. SPEC et échanges en français.
- **Typage complet** des signatures publiques (`X | None`, `list[str]`…), sans `Any` sauf nécessité.
- **Docstrings** au format NumPy pour toute fonction ou classe publique : description, `Parameters`,
  `Returns`, `Raises`, et unité des grandeurs physiques.
- **Pydantic v2** pour les modèles de données. Pas de dataclasses pour les entités du modèle.
- **Unité fixée par champ** et déclarée dans les métadonnées (`sourced(unit="m²")`) : SI, ou unité usuelle
  du métier telle que la donnent les sources (kW, kWp, L, °). Jamais d'unité implicite.
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
