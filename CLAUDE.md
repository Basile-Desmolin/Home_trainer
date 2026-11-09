# CLAUDE.md — Home trainer

Application de bureau Windows en Python 3 et PySide6. Elle pilote un home trainer (Bluetooth FTMS, Wahoo,
ANT+ FE-C), lit cardio et capteur de puissance, et déroule des séances en briques, du mode libre et des
parcours GPX. Les sorties sont exportées en `.fit` et envoyées vers Strava et Nolio.
Ce fichier est chargé à chaque session : il reste court. Le fonctionnement pour l'utilisateur est décrit
dans `README.md`.

## Lancer, tester, vérifier

- Environnement : `.venv` (installé en mode éditable). Installation complète :
  `.venv\Scripts\python.exe -m pip install -e ".[dev,gui,ble,ant]"`.
- Lancer : `.venv\Scripts\home-trainer-gui.exe` (interface) ou `.venv\Scripts\home-trainer.exe` (ligne de
  commande). Home trainer simulé disponible (`SimulatedTrainer`, `ui/power.py`, `--trainer sim`).
- Tests : `.venv\Scripts\python.exe -m pytest -q` (environ 470 tests, Qt hors écran) ; sortie longue : l'envoyer
  dans un fichier et n'afficher que les échecs et le verdict.
- Ne pas lancer l'application ni modifier `.venv` sans le demander : l'utilisateur peut être en train de rouler.

## Carte du code (`src/home_trainer/`)

| Dossier ou fichier | Contenu |
|---|---|
| `ui/app.py` (~100 Ko) | Fenêtre principale : boucle `_on_tick` (200 ms), `_refresh`, enregistrement 1 Hz, raccourcis, menus du clic droit, fin de sortie. Lire par extraits (chercher la fonction d'abord). |
| `ui/session.py` | `Sample`, `SecondSampler` (une mesure par seconde de chrono, moyennes de la seconde), déroulé et moyennes d'une sortie (séance, libre, parcours). |
| `ui/chart.py`, `ui/free_ride.py`, `ui/route_ride.py` | Courbes et écrans des trois modes. |
| `ui/indicators.py` | Catalogue d'indicateurs commun aux trois écrans et à la vignette : `RideView` (instantané construit par `_ride_view` dans `app.py`), `CATALOG` (texte de chaque carte). Un nouvel indicateur s'ajoute ici. |
| `ui/mini.py` | Vignette au premier plan pendant une sortie, application en arrière-plan : valeurs du catalogue choisies au clic droit, par mode, gardées dans le profil (`layouts`, clés `mini:…`). |
| `ui/metric.py`, `ui/gauges.py`, `ui/theme.py` | Carte de valeur `Metric`, grille `MetricGrid` (cartes placées par `Slot`, indicateur de chaque place choisi au clic droit et gardé dans le profil), barres de zones (puissance, FC ESIE), anneau, couleurs, échelle des tailles selon la fenêtre. |
| `ui/sounds.py` | Sons du bip avant un changement de brique, fabriqués par l'application (WAV), joués par QtMultimedia. |
| `ui/rescue.py` | Sortie de secours : mesures gardées toutes les 10 s dans `%APPDATA%\HomeTrainer\secours\`, proposées au lancement si la sortie n'a pas été enregistrée. |
| `ui/power.py` | Interface `PowerSource`, home trainer simulé, modèle physique : `road_speed_kmh` (vitesse d'équilibre), `next_speed_kmh` (élan et roue libre, vitesse de tous les modes). |
| `sensors/` | Protocoles : `trainer.py` (décodeurs FTMS et Cycling Power), `trainer_ble.py`, `trainer_ant.py`, `power_meter.py`, `heart_rate.py`, `zwift_click.py`. |
| `formats/` | `.fit` (séances et activités), `.erg`/`.mrc`, `.zwo`. |
| `sync/` | Comptes (secrets chiffrés par Windows, `secret.py`), file d'envoi (`outbox.py`), Strava et Nolio. |
| `ride_stats.py`, `history.py`, `profiles.py`, `gears.py`, `route.py` | NP, TSS, `LiveStats` (moyennes, max, NP au fil de la sortie), historique (copie `.bak`, fichier illisible gardé), profils (réglages d'affichage compris), braquets virtuels, GPX. |
| `logs.py` | Journal des erreurs : `%APPDATA%\HomeTrainer\journaux\home-trainer.log`, exceptions non rattrapées comprises. |

Hors application : `tools/compare_fit/`, scripts qui comparent deux `.fit` d'une même sortie (application et
Garmin) ; mode d'emploi dans son `README.md`. `packaging/` : exécutable Windows et raccourci.

Tests à connaître : `tests/test_recording_audit.py` (enregistrement d'une sortie : chrono, une mesure par
seconde, FC, pauses, cadence), `tests/test_responsiveness.py` (rapidité : redessins, courbes tenues au fil des
mesures, fenêtre réduite, fermeture), `tests/test_fit_ride_data.py` (FTP, TSS, IF ; altitude, D+, position GPS
d'un parcours dans le `.fit`).

## Données

- Sorties, profils, historique et jetons de connexion de l'utilisateur sont hors du dépôt (dossier de
  données de l'application ; `comptes.json` contient des jetons) : ne les lire qu'à la demande, jamais
  d'écriture directe. Les tests et les scripts de capture redirigent ces dossiers vers un dossier temporaire.
- Fichiers de sorties (`.fit`, `.tcx`) fournis par l'utilisateur : analyser par script, ne pas les lire en
  entier, ne pas les copier dans le dépôt.

## Règles de travail

- Langue : réponses, commentaires et commits en français ; identifiants du code en anglais, comme l'existant.
  Les commentaires disent le pourquoi, sans renvoyer à des documents de suivi hors du dépôt.
- **Lecture économe** : chercher puis lire l'extrait utile ; ne pas relire un fichier inchangé.
- **Vérification** : ne jamais déclarer vérifié ce qui n'a pas été vu ou exécuté.
- **Calculs critiques** (puissance, NP, TSS, vitesse, export `.fit`) : présenter toute modification avec un
  exemple chiffré vérifié, avant et après, et un test. Relire les `.fit` produits avec le SDK Garmin
  (`garmin-fit-sdk`).
- **Affichage** : toute modification d'un écran vient avec un aperçu visuel. Rendu hors écran par script
  (`QT_QPA_PLATFORM=offscreen`, `widget.grab()`, home trainer simulé), sans lancer l'application : capture
  avant, puis capture après, regardée avant d'être montrée. Les captures du README sont dans `docs/`.
- **Compatibilité des fichiers** : un profil, un historique ou un `comptes.json` d'une version précédente
  doit rester lisible (valeur par défaut pour un champ absent ou invalide, fichier illisible gardé de côté,
  jamais écrasé).
- **Tenir ce fichier et le README à jour** : un fichier, un dossier ou une commande ajoutés, renommés ou
  supprimés mettent à jour ce fichier dans le même commit ; une fonction visible de l'utilisateur, le README.

## Instructions de compactage

Lors d'un résumé, conserver : la tâche en cours et son plan validé ; les décisions et réponses de
l'utilisateur ; les fichiers modifiés et les fonctions concernées ; le dernier résultat des tests ; ce qui
reste à faire.
