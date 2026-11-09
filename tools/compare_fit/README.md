# Comparer deux `.fit` (application / Garmin)

Scripts qui comparent une même sortie enregistrée par l'application et par un compteur Garmin
(chrono, secondes doublées ou manquantes, puissance, bruit). Ils lisent les `.fit` avec
`garmin-fit-sdk` et ont besoin de numpy et matplotlib (`.venv\Scripts\python.exe -m pip install numpy matplotlib`).
Les fichiers de sorties restent hors du dépôt : on passe leur chemin en argument.

Depuis la racine du dépôt (`-X utf8` pour les accents dans la console Windows) :

```
.venv\Scripts\python.exe -X utf8 tools\compare_fit\inventaire.py Appli.fit Garmin.fit
.venv\Scripts\python.exe -X utf8 tools\compare_fit\compare.py Appli.fit Garmin.fit aligne.npz
.venv\Scripts\python.exe -X utf8 tools\compare_fit\derive.py Appli.fit Garmin.fit aligne.npz
.venv\Scripts\python.exe -X utf8 tools\compare_fit\bruit.py Appli.fit Garmin.fit
.venv\Scripts\python.exe -X utf8 tools\compare_fit\figure.py Appli.fit aligne.npz comparaison.png
```

| Script | Rôle |
|---|---|
| `inventaire.py` | Messages des fichiers : en-tête, appareils, session, tours, événements, champs des points. |
| `compare.py` | Décalage d'horloge, secondes doublées ou manquantes, écarts de puissance, cadence et FC, vitesse ; écrit les séries alignées (`.npz`) pour les deux suivants. |
| `derive.py` | Paquets de secondes doublées ou manquantes (dérive du chrono), décalage local, écart après lissage. |
| `bruit.py` | Variation d'une seconde à l'autre (puissance, cadence, FC) des deux fichiers. |
| `figure.py` | Figure : puissance lissée, zoom autour du maximum, retard du chrono, écart de cadence. |

Le `.npz` et la figure contiennent des données de la sortie : les écrire hors du dépôt.
