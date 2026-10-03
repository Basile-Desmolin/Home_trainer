# home-trainer

Logiciel desktop (Python, multiplateforme) pour piloter un home trainer Wahoo
en Bluetooth ou ANT+, lire des séances `.zwo`, `.erg`, `.mrc` (et `.fit`) et
en composer en briques « x min à y watts ».

## État

| Brique | État |
| --- | --- |
| Modèle de séance (briques, rampes, répétitions, cibles W ou % FTP) | ✅ `src/home_trainer/workout.py` |
| Lecture / écriture `.zwo`, `.erg`, `.mrc`, `.fit` | ✅ `src/home_trainer/formats/` |
| Notation texte des briques + ligne de commande | ✅ `bricks.py`, `cli.py` |
| Interface de séance (profil complet, temps restant, puissance, intensité ±1 %) | ✅ `src/home_trainer/ui/`, home trainer simulé |
| Capteur cardiaque Bluetooth et ANT+ (+ simulé), affiché et enregistré | ✅ `src/home_trainer/sensors/` |
| Éditeur graphique de briques | à venir |
| Pilotage Wahoo (Bluetooth FTMS, ANT+ FE-C), mode ERG | à venir |

## Installation

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

## Utilisation

```bash
# Créer une séance (le format suit l'extension)
home-trainer new sweet-spot.zwo --name "Sweet spot" "10m@50%>70% 3x(10m@90% 3m@55%) 10m@60%>40%"
home-trainer new vo2.erg --ftp 250 "15m@150 10x(30s@350 30s@150) 10m@120"

# Lire une séance (Zwift, TrainerRoad, Golden Cheetah, intervals.icu…)
home-trainer show sweet-spot.zwo --ftp 250

# Convertir
home-trainer convert sweet-spot.zwo sweet-spot.erg --ftp 250
```

Notation des briques :

| Écriture | Signification |
| --- | --- |
| `10m@150` ou `10m@150W` | 10 minutes à 150 W |
| `20m@88%` | 20 minutes à 88 % de la FTP |
| `5m30s@200-220` | 5 min 30 s entre 200 et 220 W |
| `10m@100>200` | rampe de 100 à 200 W sur 10 minutes |
| `10m@50%>75%` | rampe de 50 à 75 % de la FTP |
| `10m` | 10 minutes sans cible |
| `open@120` | jusqu'à appui sur « tour », à 120 W |
| `4x(4m@105% 2m@55%)` | répétition (imbrication possible) |

Depuis Python :

```python
from home_trainer.bricks import parse_workout
from home_trainer.formats import load_workout, save_workout

w = parse_workout("10m@150 5x(1m@300 1m@150) 10m@120", name="Test")
save_workout(w, "test.zwo", ftp=250)   # .zwo est en % FTP : la FTP sert à convertir
for seg in load_workout("test.zwo").timeline(ftp=250):
    print(seg.start_s, seg.duration_s, seg.target_w(0))
```

`Workout.timeline(ftp)` déplie les répétitions et donne, pour chaque brique,
son instant de départ et sa cible en watts ; `Segment.target_w(t)` donne la
consigne à l'instant `t` (rampes comprises). C'est l'entrée prévue pour le
mode ERG.

## Interface graphique

```bash
pip install -e ".[gui]"
home-trainer-gui                       # séance de démo
home-trainer-gui tests/data/velo-route.erg --ftp 250
home-trainer-gui --bricks "10m@150 3x(4m@105% 2m@55%) 10m@110"
```

Profil complet de la séance coloré par zones, curseur d'avancement et
puissance réalisée ; temps restant sur la brique et au total ; puissance,
cible et cadence ; intensité réglable par pas de 1 % (boutons, ou ↑ ↓,
appui long pour défiler). Espace = démarrer / pause, → = brique suivante.

Le home trainer est pour l'instant **simulé** (`ui/power.py`) : le pilote
Wahoo implémentera la même interface `PowerSource` (`set_target`, `read`).
La logique de déroulé (`ui/session.py`) ne dépend pas de Qt et est testée.

## Capteur cardiaque

```bash
pip install -e ".[gui,ble]"            # Bluetooth (bleak)
pip install -e ".[gui,ant]"            # ANT+ (openant) avec une clé USB ANT+
home-trainer-gui --hr ble              # première ceinture Bluetooth à portée
home-trainer-gui --hr ble --hr-address AA:BB:CC:DD:EE:FF
home-trainer-gui --hr ant              # première ceinture ANT+ (ou --hr-ant-id 12345)
home-trainer-gui --hr sim              # cardio simulé (défaut), --hr aucun pour le masquer
```

Le capteur se choisit aussi en cours de route avec le bouton **Cardio…**
(recherche des ceintures Bluetooth à portée). La fréquence s'affiche en
grand avec la moyenne de la séance, et sa courbe se superpose au profil
(échelle en bpm à droite). En cas de perte du signal, l'interface l'indique
et la connexion est retentée automatiquement.

- Bluetooth : service standard Heart Rate (0x180D), compatible avec les
  ceintures Polar, Garmin, Wahoo TICKR, montres en mode diffusion…
- ANT+ : profil HRM (type 120) ; ANT+ nécessite une clé USB (Garmin, CycPlus…)
  et, sous Linux, une règle udev pour y accéder sans être root.
- Le cardio simulé suit la puissance pédalée, avec l'inertie d'un vrai cœur.

`home_trainer.sensors.BackgroundSensor` est le socle commun : connexion
dans un fil dédié, reconnexion, état lisible, dernière mesure (`latest()`)
et péremption d'une mesure trop ancienne. Le pilote Wahoo pourra s'en servir.
Les trames Bluetooth et ANT+ sont décodées par des fonctions pures, testées
sans matériel (`tests/test_sensors.py`).

## Formats

| Extension | Puissance | Ce qui se perd à l'écriture |
| --- | --- | --- |
| `.zwo` (Zwift) | % FTP | plages (→ valeur moyenne) ; répétitions autres que on/off dépliées |
| `.erg` | watts | répétitions dépliées, plages (→ moyenne), noms |
| `.mrc` | % FTP | idem `.erg` |
| `.fit` | watts ou % FTP | rampes écrites en paliers d'une minute |

- Passer des watts au % FTP (ou l'inverse) demande une FTP (`--ftp`), ou celle
  indiquée dans le fichier (`FTP =` des `.erg`/`.mrc`).
- À la lecture, les motifs répétés (`.erg`, `.mrc`, `.zwo`) sont regroupés en
  répétitions, et 0 W est lu comme « pas de consigne » (roue libre).
- Les briques ouvertes (`open`) ne s'écrivent qu'en `.fit`.

## Choix techniques

- **`.zwo`, `.erg`, `.mrc`** : code maison, sans dépendance (`formats/zwo.py`,
  `formats/erg.py`).
- **Lecture `.fit`** : SDK officiel Garmin (`garmin-fit-sdk`), qui gère tout
  le protocole (CRC, en-têtes compressés, champs développeur).
- **Écriture `.fit`** : encodeur maison (`formats/fit_encoder.py`), car le SDK Python
  de Garmin ne sait pas écrire. Les fichiers produits sont validés par le
  décodeur officiel dans les tests.
- Puissance dans le fichier : `0..1000` = % FTP, `> 1000` = watts + 1000
  (convention FIT). Les cibles en zones, durées en distance et répétitions
  conditionnelles d'autres plateformes sont approximées, avec un
  avertissement.
- Prévu pour la suite : interface **PySide6 (Qt)**, Bluetooth via **bleak**
  (FTMS, et le service Wahoo propriétaire en repli), ANT+ via **openant**
  (profil FE-C) avec une clé USB ANT+.
