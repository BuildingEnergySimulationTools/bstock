# Tutoriel bstock

[`bstock_tutorial.ipynb`](bstock_tutorial.ipynb) parcourt le fonctionnement général de bstock sur
les deux sites de Nobatek : modèle du projet, création du parc, enrichissement par le RNB, la BDNB
et les DPE, représentations (`repr`, arbre HTML, `describe()`), provenance et priorité des
sources, enveloppe et systèmes, sélections et analyses, mesures et consommations, graphiques
plotly, sauvegarde et exports.

```bash
python -m pip install -e ".[tutorial,geo]"   # Jupyter, plotly, kaleido, requests (+ geo pour le GeoPackage)
jupyter lab tutorial/bstock_tutorial.ipynb
```

La partie 3 appelle les API publiques : il faut un accès à internet. Les données du client
(inventaire, audit, compteurs) sont fictives et générées dans le notebook ; les fichiers produits
vont dans le répertoire temporaire du système (`<tmp>/bstock_tutorial`).

Le notebook est versionné **avec ses sorties**, pour être lisible directement sur GitHub : les
figures plotly sont enregistrées en HTML interactif et en PNG (renderer
`notebook_connected+png`, images produites par kaleido). Avant de committer, le réexécuter en
entier depuis un noyau neuf (dans JupyterLab, *Kernel → Restart Kernel and Run All Cells*).
`tests/test_tutorial.py` vérifie qu'il est enregistré après une exécution complète, sans erreur
et avec les PNG des figures, et l'exécute en entier avec `pytest -m network`.
