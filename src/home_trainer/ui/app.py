"""Fenêtre principale : séance complète, temps restant, puissance, cardio, réglage ±1 %,
et mode libre (sans séance : puissance ERG réglée à la main par pas de 5 W,
ou pente simulée par pas de 0,5 % selon le poids saisi à côté de la FTP),
et parcours GPX (la pente de la route suit la distance parcourue).

    home-trainer-gui [seance.erg | parcours.gpx] [--profile Basile] [--ftp 250] [--weight 70] [--bricks "10m@150 3x(4m@105% 2m@55%)"]
                     [--trainer sim|ble|ant] [--trainer-address AA:BB:…] [--trainer-ant-id 12345]
                     [--hr ble|ant|aucun] [--hr-address AA:BB:…] [--hr-ant-id 12345]

Raccourcis : Espace = démarrer / pause, ↑ ou + = +1 %, ↓ ou − = −1 %,
→ ou N = brique suivante, E = ERG on / off, Ctrl+N = nouvelle séance, Ctrl+O = ouvrir,
Ctrl+E = modifier, Ctrl+S = enregistrer sous, Ctrl+L = mode libre / séance,
Ctrl+G = rouler un parcours GPX,
Ctrl+T = terminer la sortie (format, nom et dossier au choix ; .fit envoyé vers Strava / Nolio).
Pendant une sortie, le PC ne se met pas en veille (Windows).
En mode libre : ↑ ↓ = ±5 W ou ±0,5 %, Page↑ Page↓ = ±25 W ou ±2 %.
Sur un parcours : ↑ ↓ = difficulté ±10 %.
"""

from __future__ import annotations

import argparse
import sys
import threading
import time
from pathlib import Path

from PySide6.QtCore import QElapsedTimer, QLocale, QObject, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QButtonGroup, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
                               QFormLayout, QDoubleSpinBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                               QMainWindow, QMenu, QMessageBox, QProgressBar, QPushButton, QSizePolicy, QSpinBox,
                               QStackedWidget, QToolBar, QToolButton, QVBoxLayout, QWidget)

from ..bricks import BrickSyntaxError, parse_workout
from ..devices import DeviceBook, SavedDevice, ant_ident
from ..formats import FormatError, save_workout
from ..formats.activity_export import write_ride
from ..formats.fit_activity import activity_file_name
from ..heart_zones import to_power
from ..history import FILE_NAME as HISTORY_FILE, RideHistory
from ..keep_awake import KeepAwake
from ..profiles import Profile, ProfileBook
from ..ride_stats import RideSummary, summarize
from ..route import RouteError, load_route
from ..sensors import (BackgroundSensor, HeartRateReading, SensorState, Slope, Trainer,
                       open_heart_rate_sensor)
from ..sync import AccountBook, Outbox, auto_services, send_pending
from ..workout import PowerUnit, Segment, Workout
from .accounts import AccountsDialog
from .calibration import CalibrationDialog, can_calibrate
from .chart import ACCENT, CADENCE, HEART, MUTED, TEXT, WorkoutChart, hms, zone_color
from .library import Library, LibraryDialog, documents_folder
from .editor import SAVE_FILTERS, WorkoutEditor, _file_name, _filter_for
from .gauges import BrickRing, ZoneBar
from .free_ride import BIG_STEP_PCT, BIG_STEP_W, FreeRidePanel
from .heart_rate_mode import ask_heart_rate_mode
from .loader import load_workout
from .metric import Metric
from .profiles import choose_profile
from .history import HistoryDialog
from .ride_export import DISCARD, RideExport, ask_ride_export
from .power import PowerSource, SimulatedTrainer, open_power_source, road_speed_kmh
from .route_ride import RoutePanel
from .theme import GOOD, OFF, WAIT, dot, icon, install, keycaps, number_font, pill, zone_index, zone_label
from .session import (DIFFICULTY_STEP, FREE_STEP_W, GRADE_STEP_PCT, MIN_RIDE_SAMPLES, FreeMode,
                      FreeRideSession, RouteSession, State, WorkoutSession, ride_points, ride_title)

DEFAULT_BRICKS = "10m@50%>75% 3x(8m@90% 3m@55%) 5m@120% 10m@60%>45%"
DEFAULT_FTP = 250
TICK_MS = 200
CLOSE_SEND_WAIT_S = 20  # à la fermeture, temps laissé à l'envoi de la dernière sortie
ICON = Path(__file__).with_name("assets") / "icon.png"
OPEN_FILTER = ("Séances et parcours (*.zwo *.mrc *.erg *.fit *.gpx);;Séances (*.zwo *.mrc *.erg *.fit);;"
               "Parcours GPX (*.gpx);;Tous les fichiers (*)")

def describe_segment(seg: Segment | None, ftp: float, intensity_pct: int = 100) -> str:
    if seg is None:
        return "—"
    duration = "jusqu'au tour" if seg.duration_s is None else hms(seg.duration_s)
    p = seg.step.power
    hr = seg.step.heart_rate
    if hr is not None and seg.low_w is None:  # séance en FC roulée sans ERG
        start, end = seg.target_bpm(0), seg.target_bpm(seg.duration_s or 0)
        target = f"{start:.0f} bpm" if round(start) == round(end) else f"{start:.0f} → {end:.0f} bpm"
    elif p is None or seg.low_w is None:
        target = "libre"
    else:
        k = intensity_pct / 100
        start = (seg.low_w + seg.high_w) / 2 * k
        end = (seg.end_low_w + seg.end_high_w) / 2 * k
        target = f"{start:.0f} W" if round(start) == round(end) else f"{start:.0f} → {end:.0f} W"
        if p.unit is PowerUnit.FTP_PERCENT:
            pct = (p.low + p.high) / 2 * k
            target += f"  ({pct:.0f} % FTP)" if not seg.step.is_ramp else ""
        if hr is not None:
            target += f"  ·  FC {hr.mid:.0f} bpm" if seg.step.heart_rate_end is None else ""
    name = f"  ·  {seg.step.name}" if seg.step.name else ""
    return f"{duration} à {target}{name}"


class MainWindow(QMainWindow):
    def __init__(self, workout: Workout, ftp: float = DEFAULT_FTP,
                 source: PowerSource | None = None,
                 heart_rate: BackgroundSensor[HeartRateReading] | None = None,
                 book: DeviceBook | None = None, accounts: AccountBook | None = None,
                 outbox: Outbox | None = None, profiles: ProfileBook | None = None,
                 profile: Profile | None = None) -> None:
        super().__init__()
        # Avec un profil, ses comptes et son dossier des sorties remplacent `accounts` et `outbox`.
        self.profiles = profiles if profiles is not None else ProfileBook()
        self.profile: Profile | None = None
        # Sans dossier des sorties (tests), les sorties ne sont ni enregistrées ni envoyées.
        self.accounts = accounts if accounts is not None else AccountBook()
        self.outbox = outbox
        self._sync = _SyncSignals(self)
        self._sync.done.connect(self._sent)
        self._send_thread: threading.Thread | None = None
        self._send_again = False
        # Fenêtre de fin de sortie (remplaçable dans les tests) et derniers choix faits dedans.
        self.ask_ride_export = ask_ride_export
        self.ask_heart_rate_mode = ask_heart_rate_mode  # séance en FC : sans ERG ou convertie en puissance
        self._hr_to_power = False  # dernier choix fait pour une séance en FC
        self._erg_wanted = True  # ERG voulu (bouton), repris par les séances qui ont des cibles en puissance
        self._export_folder: Path | None = None  # None : dossier des sorties du profil
        self._export_fmt = "fit"
        self._asking = False
        self.keep_awake = KeepAwake()
        self.ftp = ftp
        self.book = book if book is not None else DeviceBook()  # sans fichier : rien n'est mémorisé
        self._remembered: dict[str, tuple] = {}
        self.directory = str(Path.home())  # dernier dossier ouvert ou enregistré
        self.library = Library()  # sans fichier : dossier non mémorisé (voir set_library)
        self.source: PowerSource = source or SimulatedTrainer()
        self.session = WorkoutSession(workout, ftp)
        self.free = FreeRideSession(ftp)
        self.route: RouteSession | None = None
        self._since_sample = 0.0
        self._last_reading = None
        self.heart_rate: BackgroundSensor[HeartRateReading] | None = None

        self._build_toolbar()
        self._build_body()
        self._build_shortcuts()

        self.clock = QElapsedTimer()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._on_tick)
        self.timer.start(TICK_MS)
        self.clock.start()
        self.load(workout)
        self.set_power_source(self.source)
        self.set_heart_rate_sensor(heart_rate)
        if profile is not None:
            self.set_profile(profile)  # envoie aussi les sorties restées en attente
        else:
            self.send_rides()  # sorties restées en attente la dernière fois

    # --- construction ----------------------------------------------------

    def _build_toolbar(self) -> None:
        """Bandeau du haut : onglets Séance / Libre / Parcours, Bibliothèque et menu Fichier à gauche ;
        à droite, en pastilles, le home trainer, le cardio, la FTP, le poids et le profil."""
        bar = QToolBar("Bandeau")
        bar.setObjectName("header")
        bar.setMovable(False)
        bar.setFloatable(False)
        bar.setContextMenuPolicy(Qt.PreventContextMenu)
        self.addToolBar(bar)
        if ICON.exists():
            logo = QLabel()
            logo.setObjectName("appName")
            logo.setPixmap(QIcon(str(ICON)).pixmap(28, 28))
            logo.setToolTip("Home trainer")
            bar.addWidget(logo)

        self.tabs: dict[str, QToolButton] = {}
        group = QButtonGroup(self)
        for key, text, slot, tip in (
            ("workout", "Séance", self.leave_free_ride, "La séance chargée"),
            ("free", "Libre", self.enter_free_ride, "Rouler sans séance, consigne réglée à la main (Ctrl+L)"),
            ("route", "Parcours", self.show_route, "Le parcours GPX chargé, sinon en choisir un (Ctrl+G)"),
        ):
            tab = self._header_button(text, tip, "tab")
            tab.setCheckable(True)
            tab.clicked.connect(slot)
            group.addButton(tab)
            bar.addWidget(tab)
            self.tabs[key] = tab
        self.tabs["workout"].setChecked(True)

        actions = {}
        for key, text, shortcut, slot, tip in (
            ("new", "Nouvelle séance…", QKeySequence.New, self._new_workout, "Composer une séance en briques"),
            ("open", "Ouvrir…", QKeySequence.Open, self._open_file,
             "Ouvrir une séance .zwo, .mrc, .erg ou .fit, ou un parcours .gpx"),
            ("route", "Parcours GPX…", QKeySequence("Ctrl+G"), self._open_route,
             "Rouler un parcours .gpx : le home trainer simule la pente de la route"),
            ("edit", "Modifier la séance…", QKeySequence("Ctrl+E"), self._edit_workout,
             "Modifier la séance affichée"),
            ("save", "Enregistrer sous…", QKeySequence.Save, self._save_as,
             "Enregistrer la séance affichée en .zwo, .mrc, .erg ou .fit"),
            ("library", "Bibliothèque…", QKeySequence("Ctrl+B"), self._open_library,
             "Séances du dossier de la bibliothèque : recherche, aperçu, double-clic pour rouler"),
            ("history", "Historique…", QKeySequence("Ctrl+H"), self._open_history,
             "Sorties du profil avec leur bilan, totaux par semaine, forme et records"),
        ):
            action = QAction(text, self)
            action.setShortcut(shortcut)
            action.setToolTip(tip)
            action.triggered.connect(slot)
            self.addAction(action)  # raccourci actif même menu fermé
            actions[key] = action
        library = self._header_button("Bibliothèque", actions["library"].toolTip() + " (Ctrl+B)", "tab")
        library.clicked.connect(self._open_library)
        bar.addWidget(library)
        history = self._header_button("Historique", actions["history"].toolTip() + " (Ctrl+H)", "tab")
        history.clicked.connect(self._open_history)
        bar.addWidget(history)
        menu = QMenu(self)
        for key in ("new", "open", "route", None, "edit", "save"):
            menu.addSeparator() if key is None else menu.addAction(actions[key])
        file_button = self._header_button("Fichier  ▾", "Nouvelle séance, ouvrir, modifier, enregistrer", "tab")
        file_button.setMenu(menu)
        file_button.setPopupMode(QToolButton.InstantPopup)
        bar.addWidget(file_button)

        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        bar.addWidget(spacer)

        self.source_label = self._header_button("", "Choisir ou calibrer le home trainer", "chip")
        self.source_label.clicked.connect(self._choose_trainer)
        bar.addWidget(self.source_label)
        self.heart_chip = self._header_button("", "Choisir la ceinture cardio", "chip")
        self.heart_chip.clicked.connect(self._choose_heart_rate)
        bar.addWidget(self.heart_chip)
        self.ftp_box = QSpinBox()
        self.ftp_box.setRange(50, 600)
        self.ftp_box.setSuffix(" W")
        self.ftp_box.setValue(int(self.ftp))
        self.ftp_box.editingFinished.connect(self._ftp_changed)
        bar.addWidget(self._chip_box("FTP", self.ftp_box, "FTP du cycliste : les % FTP des séances en dépendent"))
        self.weight_box = QDoubleSpinBox()
        self.weight_box.setRange(30, 200)
        self.weight_box.setDecimals(1)
        self.weight_box.setSingleStep(0.5)
        self.weight_box.setSuffix(" kg")
        self.weight_box.setLocale(QLocale(QLocale.French))  # « 68,5 kg »
        self.weight_box.setValue(self.free.rider_kg)
        self.weight_box.valueChanged.connect(self._weight_changed)
        bar.addWidget(self._chip_box("Poids", self.weight_box, "Poids du cycliste, pour la pente simulée "
                                     "(mode libre et parcours GPX ; vélo : 9 kg en plus)"))
        self.profile_action = QAction("Profil…", self)
        self.profile_action.setToolTip("Changer de cycliste : FTP, poids, comptes Strava / Nolio et sorties")
        self.profile_action.triggered.connect(self._choose_profile)
        profile_menu = QMenu(self)
        change = profile_menu.addAction("Changer de cycliste…")
        change.triggered.connect(self._choose_profile)
        accounts = profile_menu.addAction("Comptes Strava / Nolio…")
        accounts.setToolTip("Comptes vers lesquels envoyer chaque sortie terminée")
        accounts.triggered.connect(self._choose_accounts)
        profile = self._header_button("", "", "chip")
        profile.setDefaultAction(self.profile_action)
        profile.setMenu(profile_menu)
        profile.setPopupMode(QToolButton.InstantPopup)
        bar.addWidget(profile)

    @staticmethod
    def _header_button(text: str, tip: str, kind: str) -> QToolButton:
        button = QToolButton()
        button.setObjectName(kind)
        button.setText(text)
        button.setToolTip(tip)
        button.setFocusPolicy(Qt.NoFocus)
        button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        button.setCursor(Qt.PointingHandCursor)
        return button

    @staticmethod
    def _chip_box(label: str, box: QSpinBox | QDoubleSpinBox, tip: str) -> QWidget:
        """Réglage en pastille : libellé grisé puis valeur modifiable (clavier ou molette)."""
        chip = QWidget()
        chip.setObjectName("chipBox")
        chip.setAttribute(Qt.WA_StyledBackground, True)
        chip.setToolTip(tip)
        row = QHBoxLayout(chip)
        row.setContentsMargins(0, 0, 12, 0)
        row.setSpacing(4)
        row.addWidget(QLabel(label))
        box.setAlignment(Qt.AlignRight)
        box.setMinimumWidth(box.fontMetrics().horizontalAdvance("000,0 kg") + 8)
        row.addWidget(box)
        return chip

    def _build_body(self) -> None:
        self.pages = QStackedWidget()
        self.setCentralWidget(self.pages)
        root = QWidget()
        self.pages.addWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(14, 10, 14, 14)
        layout.setSpacing(10)

        heading = QHBoxLayout()
        self.title = QLabel()
        self.title.setObjectName("title")
        self.subtitle = QLabel()
        self.subtitle.setObjectName("metricSub")
        heading.addWidget(self.title)
        heading.addWidget(self.subtitle, 1, Qt.AlignBottom)
        layout.addLayout(heading)

        grid = QGridLayout()
        grid.setSpacing(10)
        self.m_power = Metric("PUISSANCE", 64)
        self.power_zones = ZoneBar()
        self.m_power.add(self.power_zones)
        self.m_target = Metric("CIBLE", 64, ACCENT)
        self.step_ring = BrickRing(124)
        self.step_ring.setToolTip("Temps restant sur la brique")
        self.m_target.row.addWidget(self.step_ring, 0, Qt.AlignTop)
        self.zone_tag = QLabel()
        self.next_label = QLabel()
        self.next_label.setObjectName("metricSub")
        tag_row = QHBoxLayout()
        tag_row.addWidget(self.zone_tag)
        tag_row.addStretch(1)
        self.m_target.body.addLayout(tag_row)
        self.m_target.add(self.next_label)
        self.m_cadence = Metric("CADENCE", 34, CADENCE)
        self.m_heart = Metric("CARDIO", 34, HEART)
        self.m_total = Metric("RESTE AU TOTAL", 34)
        self.total_bar = QProgressBar()
        self.total_bar.setTextVisible(False)
        self.total_bar.setFixedHeight(6)
        self.total_bar.setRange(0, 1000)
        self.m_total.add(self.total_bar)
        grid.addWidget(self.m_power, 0, 0, 2, 1)
        grid.addWidget(self.m_target, 0, 1, 2, 1)
        grid.addWidget(self.m_cadence, 0, 2)
        grid.addWidget(self.m_heart, 0, 3)
        grid.addWidget(self._build_intensity(), 1, 2)
        grid.addWidget(self.m_total, 1, 3)
        for c, stretch in enumerate((28, 30, 21, 21)):
            grid.setColumnStretch(c, stretch)
        layout.addLayout(grid)

        self.current_label = QLabel()
        self.current_label.setObjectName("current")
        layout.addWidget(self.current_label)

        self.chart = WorkoutChart()
        layout.addWidget(self.chart, 1)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.play_button = QPushButton("Démarrer")
        self.play_button.setObjectName("play")
        self.play_button.clicked.connect(self._toggle)
        self.erg_button = QPushButton("ERG on")
        self.erg_button.setObjectName("erg")
        self.erg_button.setCheckable(True)
        self.erg_button.setChecked(True)
        self.erg_button.setToolTip("ERG on : le home trainer impose la cible.\nERG off : résistance libre, "
                                   "la séance continue et la cible est à suivre à la main (E)")
        self.erg_button.clicked.connect(self.toggle_erg)
        self.next_button = QPushButton("Brique suivante")
        self.next_button.setIcon(icon("next"))
        self.next_button.clicked.connect(self._next_step)
        self.reset_button = QPushButton("Recommencer")
        self.reset_button.setIcon(icon("restart"))
        self.reset_button.clicked.connect(self._reset)
        self.finish_button = QPushButton("Terminer")
        self.finish_button.setIcon(icon("flag"))
        self.finish_button.setToolTip(FINISH_TIP)
        self.finish_button.clicked.connect(self.finish_ride)
        self.free_button = QPushButton("Mode libre")
        self.free_button.setToolTip("Rouler sans séance en réglant la puissance à la main (Ctrl+L)")
        self.free_button.clicked.connect(self.enter_free_ride)
        for b in (self.play_button, self.erg_button, self.next_button, self.reset_button, self.finish_button, self.free_button):
            b.setFocusPolicy(Qt.NoFocus)
            b.setCursor(Qt.PointingHandCursor)
            buttons.addWidget(b)
        buttons.addStretch(1)
        hint = QLabel(keycaps("[Espace] pause   [↑] [↓] ±1 %   [→] brique suivante   [E] ERG"))
        buttons.addWidget(hint)
        layout.addLayout(buttons)

        self.free_panel = FreeRidePanel()
        self.free_panel.chart.set_session(self.free)
        self.free_panel.play_button.clicked.connect(self._toggle)
        self.free_panel.reset_button.clicked.connect(self._reset_free_ride)
        self.free_panel.back_button.clicked.connect(self.leave_free_ride)
        self.free_panel.finish_button.setToolTip(FINISH_TIP)
        self.free_panel.finish_button.clicked.connect(self.finish_ride)
        for b, direction, big in self.free_panel.adjust_buttons:
            b.clicked.connect(lambda _=False, d=direction, big=big: self.nudge_free(d, big))
        for mode, b in self.free_panel.mode_buttons.items():
            b.clicked.connect(lambda _=False, m=mode: self.set_free_mode(m))
        self.pages.addWidget(self.free_panel)

        self.route_panel = RoutePanel()
        self.route_panel.play_button.clicked.connect(self._toggle)
        self.route_panel.reset_button.clicked.connect(self._reset_route)
        self.route_panel.back_button.clicked.connect(self.leave_free_ride)
        self.route_panel.finish_button.setToolTip(FINISH_TIP)
        self.route_panel.finish_button.clicked.connect(self.finish_ride)
        for b, delta in self.route_panel.difficulty_buttons:
            b.clicked.connect(lambda _=False, d=delta: self.adjust_difficulty(d))
        self.pages.addWidget(self.route_panel)

    def _build_intensity(self) -> QWidget:
        box = QFrame()
        box.setObjectName("metric")
        layout = QVBoxLayout(box)
        layout.setContentsMargins(16, 10, 16, 12)
        layout.setSpacing(4)
        title = QLabel("INTENSITÉ")
        title.setObjectName("metricTitle")
        layout.addWidget(title)
        row = QHBoxLayout()
        self.minus_button = QPushButton("−1")
        self.plus_button = QPushButton("+1")
        self.intensity_label = QLabel("100 %")
        self.intensity_label.setFont(number_font(40))
        self.intensity_label.setAlignment(Qt.AlignCenter)
        for b, delta in ((self.minus_button, -1), (self.plus_button, +1)):
            b.setObjectName("adjust")
            b.setFocusPolicy(Qt.NoFocus)
            b.setToolTip(f"{delta:+d} % sur toute la suite de la séance".replace("-", "−"))
            b.setAutoRepeat(True)  # rester appuyé fait défiler les pourcents
            b.setAutoRepeatDelay(400)
            b.setAutoRepeatInterval(120)
            b.clicked.connect(lambda _=False, d=delta: self.adjust(d))
        row.addWidget(self.minus_button)
        row.addWidget(self.intensity_label, 1)
        row.addWidget(self.plus_button)
        layout.addStretch(1)
        layout.addLayout(row)
        layout.addStretch(1)
        return box

    def _build_shortcuts(self) -> None:
        # ↑ ↓ : ±1 % sur une séance, ±5 W en mode libre ; Page↑ Page↓ : ±5 % ou ±25 W.
        for keys, slot in (((Qt.Key_Space,), self._toggle),
                           ((Qt.Key_Up, Qt.Key_Plus, Qt.Key_Equal), lambda: self._step(+1)),
                           ((Qt.Key_Down, Qt.Key_Minus), lambda: self._step(-1)),
                           ((Qt.Key_PageUp,), lambda: self._step(+5)),
                           ((Qt.Key_PageDown,), lambda: self._step(-5)),
                           ((Qt.Key_Right, Qt.Key_N), self._next_step)):
            for key in keys:
                QShortcut(QKeySequence(key), self, activated=slot)
        QShortcut(QKeySequence(Qt.Key_E), self, activated=self.toggle_erg)
        QShortcut(QKeySequence("Ctrl+L"), self, activated=self._toggle_free_ride)
        QShortcut(QKeySequence("Ctrl+T"), self, activated=self.finish_ride)

    # --- actions ---------------------------------------------------------

    @property
    def free_ride(self) -> bool:
        """Vrai quand le mode libre est affiché (et c'est lui qui pilote le home trainer)."""
        return self.pages.currentWidget() is self.free_panel

    @property
    def route_ride(self) -> bool:
        """Vrai quand un parcours GPX est affiché (et c'est lui qui pilote le home trainer)."""
        return self.route is not None and self.pages.currentWidget() is self.route_panel

    @property
    def active(self) -> WorkoutSession | FreeRideSession | RouteSession:
        if self.route_ride:
            return self.route
        return self.free if self.free_ride else self.session

    def load(self, workout: Workout, *, end_ride: bool = True) -> None:
        if end_ride:  # la séance en cours, même inachevée, est enregistrée avant d'être remplacée
            self.end_ride(self.session)
        intensity = self.session.intensity_pct
        self.session = WorkoutSession(workout, self.ftp, self.free.rider_kg)
        self.session.intensity_pct = intensity
        # Sans cible en puissance (séance en FC roulée aux vitesses), pas d'ERG possible.
        self.session.erg = self._erg_wanted and workout.has_power_targets
        self.erg_button.setEnabled(workout.has_power_targets)
        self._since_sample = 0.0
        self.chart.set_session(self.session)
        self.title.setText(workout.name)
        count = len(self.session.segments)
        self.subtitle.setText(f"   {hms(self.session.total_s)}  ·  {count} brique{'s' if count > 1 else ''}")
        if not self.free_ride:
            self.setWindowTitle(f"{workout.name} — Home trainer")
        self._refresh()

    def adjust(self, delta: int) -> None:
        self.session.adjust_intensity(delta)
        self._push_target()
        self._refresh()

    def toggle_erg(self) -> None:
        """Séance : ERG on (le home trainer impose la cible) ou off (résistance libre, cible à suivre)."""
        if self.free_ride or self.route_ride or not self.session.workout.has_power_targets:
            return
        self._erg_wanted = self.session.toggle_erg()
        self._push_target()
        self._refresh()

    def nudge_free(self, direction: int, big: bool = False) -> None:
        """Mode libre : un cran de plus (+1) ou de moins (−1), petit ou grand selon le réglage."""
        if self.free.mode is FreeMode.ERG:
            self.free.adjust_target(direction * (BIG_STEP_W if big else FREE_STEP_W))
        else:
            self.free.adjust_grade(direction * (BIG_STEP_PCT if big else GRADE_STEP_PCT))
        self._push_target()
        self._refresh()

    def set_free_mode(self, mode: FreeMode) -> None:
        self.free.mode = mode
        self._push_target()
        self._refresh()

    def adjust_difficulty(self, delta: int) -> None:
        if self.route is not None:
            self.route.adjust_difficulty(delta)
            self._push_target()
            self._refresh()

    def _weight_changed(self, kg: float) -> None:
        if self.profile is not None and self.profile.weight_kg != kg:
            self.profile.weight_kg = kg
            self.profiles.save()
        self.free.rider_kg = kg
        self.session.rider_kg = kg
        if self.route is not None:
            self.route.rider_kg = kg
        self._push_target()
        self._refresh()

    def _step(self, notches: int) -> None:
        if self.route_ride:
            self.adjust_difficulty(DIFFICULTY_STEP if notches > 0 else -DIFFICULTY_STEP)
        elif self.free_ride:
            self.nudge_free(1 if notches > 0 else -1, big=abs(notches) > 1)
        else:
            self.adjust(notches)

    def enter_free_ride(self) -> None:
        """Passe en mode libre ; la séance est mise en pause là où elle en est."""
        if self.free_ride:
            return
        self.active.pause()
        self.pages.setCurrentWidget(self.free_panel)
        self.setWindowTitle("Mode libre — Home trainer")
        self._push_target()
        self._refresh()

    def leave_free_ride(self) -> None:
        """Revient à la séance ; le mode libre ou le parcours est mis en pause (consigne, temps et distance
        sont gardés)."""
        if self.pages.currentIndex() == 0:
            return
        self.active.pause()
        self.pages.setCurrentIndex(0)
        self.setWindowTitle(f"{self.session.workout.name} — Home trainer")
        self._push_target()
        self._refresh()

    def show_route(self) -> None:
        """Revient au parcours chargé (en pause, tel qu'on l'a laissé) ; sans parcours, en propose un."""
        if self.route is None:
            self._open_route()
            self._refresh()  # onglet Parcours décoché si rien n'a été choisi
            return
        if self.route_ride:
            return
        self.active.pause()
        self.pages.setCurrentWidget(self.route_panel)
        self.setWindowTitle(f"{self.route.route.name} — Home trainer")
        self._push_target()
        self._refresh()

    def ride_route(self, route) -> None:
        """Affiche ce parcours, prêt à partir ; ce qui roulait est mis en pause."""
        self.active.pause()
        if self.route is not None:  # le parcours précédent, même inachevé, est enregistré
            self.end_ride(self.route)
        self.route = RouteSession(route, self.ftp, self.weight_box.value())
        self.route_panel.set_session(self.route)
        self._since_sample = 0.0
        self.pages.setCurrentWidget(self.route_panel)
        self.setWindowTitle(f"{route.name} — Home trainer")
        self._push_target()
        self._refresh()

    def _reset_route(self) -> None:
        if self.route is not None:
            old = self.route
            self.ride_route(old.route)
            self.route.difficulty_pct = old.difficulty_pct

    def _toggle_free_ride(self) -> None:
        self.leave_free_ride() if self.free_ride else self.enter_free_ride()

    def _reset_free_ride(self) -> None:
        old = self.free
        self.end_ride(old)
        self.free = FreeRideSession(self.ftp, old.target_w, old.rider_kg, old.mode, old.grade_pct)
        self.free_panel.chart.set_session(self.free)
        self._since_sample = 0.0
        self._push_target()
        self._refresh()

    def _toggle(self) -> None:
        if self.route_ride:
            if self.route.state is State.FINISHED:
                self._reset_route()
            self.route.toggle()
            self.clock.restart()
            self._push_target()
            self._refresh()
            return
        if self.free_ride:
            self.free.toggle()
            self.clock.restart()
            self._push_target()
            self._refresh()
            return
        if self.session.state is State.FINISHED:
            self._reset()
        self.session.toggle()
        self.clock.restart()
        self._refresh()

    def _next_step(self) -> None:
        if self.free_ride or self.route_ride:
            return
        self.session.next_step()
        self._push_target()
        self._refresh()

    def _reset(self) -> None:
        self.load(self.session.workout)

    def _ftp_changed(self) -> None:
        if self.ftp_box.value() == self.ftp:
            return
        self.ftp = self.ftp_box.value()
        if self.profile is not None:
            self.profile.ftp = int(self.ftp)
            self.profiles.save()
        self.free.ftp = self.ftp
        if self.route is not None:
            self.route.ftp = self.ftp
        s = self.session
        index, step_elapsed, elapsed, state, samples = (s.index, s.step_elapsed_s, s.elapsed_s,
                                                         s.state, s.samples)
        self.load(s.workout, end_ride=False)
        self.session.exported = s.exported
        self.session.export_asked = s.export_asked
        # Garder l'avancement : seule la conversion % FTP → W change.
        self.session.index, self.session.step_elapsed_s, self.session.elapsed_s = index, step_elapsed, elapsed
        self.session.state, self.session.samples = state, samples
        self._refresh()

    def set_power_source(self, source: PowerSource) -> None:
        """Remplace le home trainer (simulé ou réel) et le démarre."""
        if source is not self.source and isinstance(self.source, Trainer):
            self.source.stop()
        self.source = source
        self._last_reading = None
        if isinstance(source, Trainer):
            source.start()
        self._push_target()
        self._refresh()

    def _choose_trainer(self) -> None:
        dialog = TrainerDialog(self.book, self, current=self.source,
                               current_name=self._display_name("trainer", self.source),
                               before_calibration=self._pause_for_calibration)
        if dialog.exec() == QDialog.Accepted:
            self.set_power_source(dialog.sensor())

    def _pause_for_calibration(self) -> None:
        """La séance se met en pause : pendant la calibration, c'est le home trainer qui mène."""
        if self.active.state is State.RUNNING:
            self.active.pause()
            self._push_target()
            self._refresh()

    def set_heart_rate_sensor(self, sensor: BackgroundSensor[HeartRateReading] | None) -> None:
        """Remplace le capteur cardio (None = aucun) et le démarre."""
        if self.heart_rate is not None:
            self.heart_rate.stop()
        self.heart_rate = sensor
        if sensor is not None:
            sensor.start()
        self._refresh()

    def _choose_heart_rate(self) -> None:
        dialog = HeartRateDialog(self.book, self)
        if dialog.exec() == QDialog.Accepted:
            self.set_heart_rate_sensor(dialog.sensor())

    def set_library(self, library: Library) -> None:
        """Bibliothèque des séances : ouvertures et enregistrements partent de son dossier."""
        self.library = library
        if library.ensure_folder():
            self.directory = str(library.folder)

    def _open_library(self) -> None:
        dialog = LibraryDialog(self.library, self.ftp, self)
        accepted = dialog.exec() == QDialog.Accepted
        if self.library.folder.is_dir():
            self.directory = str(self.library.folder)
        if accepted and dialog.chosen_path() is not None:
            self.open_path(str(dialog.chosen_path()))

    def _remember(self, role: str, sensor) -> None:
        """Mémorise l'appareil dès qu'il est connecté (adresse ou numéro découverts à la connexion)."""
        if not isinstance(sensor, BackgroundSensor) or sensor.state is not SensorState.CONNECTED:
            return
        if sensor.device_id is None or sensor.device_kind is None:
            return
        key = (id(sensor), sensor.device_kind, sensor.device_id)
        if self._remembered.get(role) == key:
            return
        self._remembered[role] = key
        self.book.remember(role, sensor.device_kind, sensor.device_id, sensor.name)
        self.book.save()

    def _display_name(self, role: str, sensor) -> str:
        """Nom choisi par l'utilisateur pour cet appareil, sinon celui du pilote."""
        saved = self.book.display_name(role, getattr(sensor, "device_kind", None) or "",
                                       getattr(sensor, "device_id", None))
        return saved or sensor.name

    def closeEvent(self, event) -> None:  # noqa: N802 (API Qt)
        self.keep_awake.set(False)
        self.end_ride(self.session, send=False)
        self.end_ride(self.free, send=False)
        if self.route is not None:
            self.end_ride(self.route, send=False)
        self.send_rides(wait_s=CLOSE_SEND_WAIT_S)
        if self.heart_rate is not None:
            self.heart_rate.stop()
        if isinstance(self.source, Trainer):
            self.source.stop()
        super().closeEvent(event)

    def _open_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Ouvrir une séance", self.directory, OPEN_FILTER)
        if path:
            self.open_path(path)

    def _open_route(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Rouler un parcours", self.directory,
                                              "Parcours GPX (*.gpx);;Tous les fichiers (*)")
        if path:
            self.open_path(path)

    def open_path(self, path: str) -> bool:
        if Path(path).suffix.lower() == ".gpx":
            return self.open_route(path)
        warnings: list[str] = []
        try:
            workout = load_workout(path, warnings)
        except Exception as e:  # noqa: BLE001 (tout échec de lecture est montré à l'utilisateur)
            QMessageBox.warning(self, "Lecture impossible", f"{Path(path).name} : {e}")
            return False
        self.directory = str(Path(path).parent)
        if warnings:
            QMessageBox.information(self, "À savoir", "\n".join(warnings))
        if workout.uses_heart_rate and not workout.has_power_targets:
            workout = self._heart_rate_workout(workout)
            if workout is None:
                return False
        self.leave_free_ride()
        self.load(workout)
        return True

    def _heart_rate_workout(self, workout: Workout) -> Workout | None:
        """Séance aux cibles en FC : telle quelle (sans ERG, aux vitesses) ou convertie en puissance
        d'après la FC max du profil (demandée, puis gardée dans le profil). None si l'on renonce."""
        hr_max = self.profile.hr_max if self.profile is not None else None
        choice = self.ask_heart_rate_mode(self, workout.name, hr_max, self._hr_to_power)
        if choice is None:
            return None
        self._hr_to_power = choice.to_power
        if not choice.to_power:
            return workout
        if self.profile is not None and self.profile.hr_max != choice.hr_max:
            self.profile.hr_max = choice.hr_max
            self.profiles.save()
        self._erg_wanted = True  # converties en puissance pour être roulées en ERG
        return to_power(workout, choice.hr_max)

    def open_route(self, path: str) -> bool:
        try:
            route = load_route(path)
        except (RouteError, OSError) as e:
            QMessageBox.warning(self, "Lecture impossible", f"{Path(path).name} : {e}")
            return False
        self.directory = str(Path(path).parent)
        self.ride_route(route)
        return True

    def _new_workout(self) -> None:
        # Une nouvelle séance s'enregistre par défaut dans la bibliothèque.
        if self.library.folder.is_dir():
            self.directory = str(self.library.folder)
        self._open_editor(None)

    def _edit_workout(self) -> None:
        self._open_editor(self.session.workout)

    def _open_editor(self, workout: Workout | None) -> None:
        if self.active.state is State.RUNNING:
            self.active.pause()
            self._push_target()
            self._refresh()
        editor = WorkoutEditor(workout, self.ftp, self, directory=self.directory)
        accepted = editor.exec() == QDialog.Accepted
        self.directory = editor.directory
        if editor.ftp != self.ftp:
            self.ftp_box.setValue(int(editor.ftp))
            self._ftp_changed()
        result = editor.workout()
        if accepted and result is not None:
            self.leave_free_ride()
            self.load(result)

    def _save_as(self) -> None:
        workout = self.session.workout
        default = Path(self.directory) / _file_name(workout.name)
        path, chosen = QFileDialog.getSaveFileName(self, "Enregistrer la séance", str(default.with_suffix("")),
                                                   ";;".join(SAVE_FILTERS), _filter_for(".zwo"))
        if not path:
            return
        if Path(path).suffix.lower() not in SAVE_FILTERS.values():
            path += SAVE_FILTERS.get(chosen, ".zwo")
        try:
            save_workout(workout, path, ftp=self.ftp)
        except (FormatError, ValueError, OSError) as e:
            QMessageBox.warning(self, "Enregistrement impossible", f"{Path(path).name} : {e}")
            return
        self.directory = str(Path(path).parent)
        self.statusBar().showMessage(f"Enregistrée : {path}", 8000)

    # --- sorties : .fit d'activité et envoi vers Strava / Nolio -------------

    def finish_ride(self) -> None:
        """Bouton « Terminer » : la sortie en cours est enregistrée là où on le choisit et envoyée,
        puis on repart de zéro ; « Annuler » dans la fenêtre ramène à la sortie, en pause."""
        active = self.active
        if active.state is State.RUNNING:
            active.pause()
            self._push_target()
            self._refresh()
        if len(active.samples) < MIN_RIDE_SAMPLES:
            self.statusBar().showMessage("Rien à enregistrer : la sortie n'a pas commencé", 8000)
        elif not self.save_ride(active):
            return
        if active is self.free:
            self._reset_free_ride()
        elif active is self.route:
            self._reset_route()
        else:
            self._reset()

    def save_ride(self, session: WorkoutSession | FreeRideSession | RouteSession) -> bool:
        """Demande format, nom et dossier puis enregistre ; False si l'utilisateur revient à la sortie."""
        if self.outbox is None or session.exported or len(session.samples) < MIN_RIDE_SAMPLES:
            return True
        title, _ = ride_title(session)
        start = next((x.at for x in session.samples if x.at is not None), time.time())
        default = RideExport(self._export_folder or self.outbox.dir,
                             Path(activity_file_name(start, title)).stem, self._export_fmt)
        summary = self.ride_summary(session)
        history = self.ride_history()
        if history is not None:
            history.mark_records(summary)
        choice = self.ask_ride_export(self, default, summary)
        if choice is None:
            return False
        if choice == DISCARD:
            session.exported = True
            self.statusBar().showMessage("Sortie non enregistrée", 8000)
            return True
        self._export_fmt = choice.fmt
        self._export_folder = None if Path(choice.folder) == self.outbox.dir else Path(choice.folder)
        return self.end_ride(session, export=choice)  # en cas d'échec, on reste sur la sortie

    def end_ride(self, session: WorkoutSession | FreeRideSession | RouteSession, send: bool = True,
                 export: RideExport | None = None) -> bool:
        """Enregistre la sortie (une seule fois) puis l'envoie aux comptes connectés.

        Sans `export`, en .fit dans le dossier des sorties ; avec, au format, au nom et dans le
        dossier choisis (et, si ce n'est pas un .fit, une copie .fit dans le dossier des sorties
        pour Strava / Nolio). False si l'écriture a échoué."""
        if self.outbox is None or session.exported or len(session.samples) < MIN_RIDE_SAMPLES:
            return True
        session.exported = True
        title, description = ride_title(session)
        points = ride_points(session.samples)
        try:
            if export is None:
                path = self.outbox.add(points, title, description)
            elif export.fmt == "fit":
                path = self.outbox.add(points, title, description, dest=export.path)
            else:
                export.path.parent.mkdir(parents=True, exist_ok=True)
                path = write_ride(points, export.path)
                self.outbox.add(points, title, description)
        except (OSError, ValueError) as e:
            session.exported = False  # on pourra réessayer avec « Terminer »
            where = export.path if export is not None else self.outbox.dir
            QMessageBox.warning(self, "Sortie non enregistrée", f"{where} : {e}")
            return False
        shown = path.name if path.parent == self.outbox.dir.absolute() else str(path)
        history = self.ride_history()
        if history is not None:
            summary = self.ride_summary(session)
            summary.file = str(Path(path).absolute())
            history.add(summary)
        if not auto_services(self.accounts):
            self.statusBar().showMessage(f"Sortie enregistrée : {shown} (pour l'envoyer vers Strava ou "
                                         f"Nolio, connectez un compte : Profil → Comptes Strava / Nolio…)", 15000)
        else:
            self.statusBar().showMessage(f"Sortie enregistrée : {shown}, envoi en cours…", 15000)
        if send:
            self.send_rides()
        return True

    def ride_summary(self, session: WorkoutSession | FreeRideSession | RouteSession) -> RideSummary:
        """Bilan de la sortie : chiffres, temps par zone, meilleures puissances."""
        title, _ = ride_title(session)
        start = next((x.at for x in session.samples if x.at is not None), time.time())
        return summarize(session.samples, self.ftp, title, start)

    def ride_history(self) -> RideHistory | None:
        """Historique du profil, dans son dossier des sorties (None sans dossier des sorties)."""
        if self.outbox is None:
            return None
        return RideHistory.load(self.outbox.dir / HISTORY_FILE)

    def _open_history(self) -> None:
        history = self.ride_history()
        if history is None:
            self.statusBar().showMessage("Pas d'historique : les sorties ne sont pas enregistrées", 8000)
            return
        HistoryDialog(history, self.profile.name if self.profile else "", self).exec()

    def _ask_at_end(self, session: WorkoutSession | RouteSession) -> None:
        """Fin de séance ou arrivée du parcours : la fenêtre d'enregistrement s'ouvre d'elle-même, une fois."""
        if self._asking or session.exported or session.export_asked:
            return
        session.export_asked = True
        self._asking = True

        def ask() -> None:
            try:
                self.save_ride(session)
            finally:
                self._asking = False

        QTimer.singleShot(0, ask)  # hors du tic de l'horloge : la fenêtre est modale

    def send_rides(self, wait_s: float = 0) -> None:
        """Envoie en tâche de fond ce qui attend ; `wait_s` > 0 attend la fin (fermeture de l'appli)."""
        if self.outbox is None or not auto_services(self.accounts):
            return
        if self._send_thread is not None and self._send_thread.is_alive():
            self._send_again = True
        else:
            outbox, accounts, signals = self.outbox, self.accounts, self._sync

            def work() -> None:
                reports = send_pending(outbox, accounts)
                try:
                    signals.done.emit(reports)
                except RuntimeError:  # fenêtre déjà fermée
                    pass

            self._send_thread = threading.Thread(target=work, daemon=True, name="envoi des sorties")
            self._send_thread.start()
        if wait_s > 0:
            deadline = time.monotonic() + wait_s
            while self._send_thread.is_alive() and time.monotonic() < deadline:
                self._send_thread.join(0.2)
            if self._send_again and not self._send_thread.is_alive():
                self._send_again = False
                send_pending(self.outbox, self.accounts)

    def _sent(self, reports: list) -> None:
        if reports:
            text = " · ".join(str(r) for r in reports[-4:])
            failed = [r for r in reports if not r.ok]
            if failed:
                text += " (nouvel essai au prochain lancement, ou Profil → Comptes Strava / Nolio…)"
            self.statusBar().showMessage(text, 20000)
        if self._send_again:
            self._send_again = False
            self.send_rides()

    # --- profils -----------------------------------------------------------

    def set_profile(self, profile: Profile) -> None:
        """Roule avec ce profil : sa FTP, son poids, ses comptes et son dossier des sorties.
        Ce qui roulait pour le profil d'avant est enregistré dans ses sorties à lui."""
        changed = self.profile is not None and self.profile.id != profile.id
        if changed:
            for session in (self.session, self.free, self.route):
                if session is not None:
                    session.pause()
                    self.end_ride(session)
        self.profile = profile
        self.accounts = AccountBook.load(self.profiles.accounts_path(profile))
        self.outbox = Outbox(self.profiles.rides_dir(profile))
        self._export_folder = None
        if changed:  # on repart de zéro (rien à enregistrer : c'est déjà fait)
            self._reset_free_ride()
            self._reset_route()
            self.load(self.session.workout)
        self.ftp_box.setValue(int(profile.ftp))
        self._ftp_changed()
        self.weight_box.setValue(profile.weight_kg)
        self.profile_action.setText(f"Profil : {profile.name}")
        self.statusBar().showMessage(f"Profil : {profile.name}", 5000)
        self.send_rides()

    def _choose_profile(self) -> None:
        profile = choose_profile(self.profiles, self.profile, self)
        if profile is not None:
            self.set_profile(profile)

    def _choose_accounts(self) -> None:
        outbox = self.outbox if self.outbox is not None else Outbox()
        dialog = AccountsDialog(self.accounts, outbox, self)
        dialog.send_requested.connect(self.send_rides)
        dialog.exec()

    # --- boucle ----------------------------------------------------------

    def _on_tick(self) -> None:
        dt = self.clock.restart() / 1000
        active = self.active
        active.tick(dt)
        self._push_target()
        running = active.state is State.RUNNING
        # Un vrai home trainer est lu en permanence (échauffement, pause) ; le simulateur seulement en séance.
        reading = self.source.read(dt) if running or isinstance(self.source, Trainer) else None
        self._last_reading = reading
        if reading is not None and running:
            self._since_sample += dt
            if self._since_sample >= 1.0:
                self._since_sample -= 1.0
                heart = self.heart_rate.latest() if self.heart_rate is not None else None
                active.record(reading.power_w, reading.cadence_rpm, heart.bpm if heart else None,
                              self._speed(reading))
            if active is self.route:
                active.ride(dt, reading.power_w)
                if active.state is State.FINISHED:
                    self._push_target()  # arrivée : résistance libre
        for session in (self.session, self.route):  # fin de séance ou arrivée : fenêtre d'enregistrement
            if session is not None and session.state is State.FINISHED:
                self._ask_at_end(session)
        # Pas de veille tant qu'une sortie est en cours, même en pause.
        self.keep_awake.set(running or (active.state is State.PAUSED and bool(active.samples)))
        self._refresh()

    def _speed(self, reading) -> float:
        """Vitesse donnée par le home trainer, sinon celle d'un cycliste de ce poids sur le plat (ou la pente)."""
        if self.route_ride:  # sur un parcours, la distance suit la vitesse simulée de la route
            return round(self.route.speed_kmh, 2)
        speed = getattr(reading, "speed_kmh", None)
        if speed is not None:
            return speed
        grade = self.free.grade_pct if self.free_ride and self.free.mode is FreeMode.SLOPE else 0.0
        return round(road_speed_kmh(reading.power_w, Slope(grade, self.free.rider_kg)), 2)

    def _push_target(self) -> None:
        # Hors séance (avant le départ, en pause), le home trainer reste en résistance libre.
        active = self.active
        running = active.state is State.RUNNING
        self.source.set_target(active.command if running else None)

    def _refresh(self) -> None:
        self._refresh_source()
        self._refresh_heart_chip()
        page = "route" if self.route_ride else "free" if self.free_ride else "workout"
        if not self.tabs[page].isChecked():
            self.tabs[page].setChecked(True)
        if self.route_ride:
            self.route_panel.refresh(self.route, self._last_reading)
            self._refresh_heart_rate(self.route_panel.m_heart, self.route)
            return
        if self.free_ride:
            self.free_panel.refresh(self.free, self._last_reading)
            self._refresh_heart_rate(self.free_panel.m_heart, self.free)
            return
        s = self.session
        r = self._last_reading
        self.m_power.set(f"{r.power_w:.0f} W" if r else "—", f"{r.power_w / self.ftp * 100:.0f} % FTP" if r else "")
        self.power_zones.set_zone(zone_index(r.power_w if r else None, self.ftp))
        target = s.target_w
        base = s.base_target_w
        bpm = s.target_bpm
        sub = "" if base is None or s.intensity_pct == 100 else f"séance : {base:.0f} W  ({s.intensity_pct} %)"
        color = None if target is None or s.state is State.FINISHED else zone_color(target, self.ftp)
        if target is None and bpm is not None:  # séance en FC roulée aux vitesses
            self.m_target.set(f"{bpm:.0f} bpm", "FC cible · résistance libre, aux vitesses")
            self.m_target.title.setText("CIBLE FC")
            self.m_target.set_color(HEART)
        else:
            if bpm is not None:
                sub = f"FC cible {bpm:.0f} bpm" + (f"  ·  {sub}" if sub else "")
            if not s.erg:
                sub = "ERG off : à suivre à la main" + (f"  ·  {sub}" if sub else "")
            self.m_target.set("libre" if target is None else f"{target:.0f} W", sub)
            self.m_target.title.setText("CIBLE" if s.erg else "CIBLE (ERG OFF)")
            # Récup (zone 1, grise) : chiffres en blanc, sinon ils auraient l'air éteints.
            self.m_target.set_color(TEXT if color is None or zone_index(target, self.ftp) == 0 else color)
        self.m_target.set_tint(color)
        self.m_power.set_tint(color)
        if color is None:
            self.zone_tag.hide()
        else:
            self.zone_tag.setText(f"{zone_label(target, self.ftp)}  ·  {target / self.ftp * 100:.0f} % FTP")
            self.zone_tag.setStyleSheet(pill(color))
            self.zone_tag.show()
        self.erg_button.setChecked(s.erg)
        self.erg_button.setText("ERG on" if s.erg else "ERG off")
        self.erg_button.setIcon(icon("check", "#6fd3a3") if s.erg else QIcon())
        remaining = s.step_remaining_s
        seg = s.segment
        fraction = (remaining / seg.duration_s if remaining is not None and seg is not None and seg.duration_s
                    else None)
        brick = f"brique {min(s.index + 1, len(s.segments))} / {len(s.segments)}"
        self.step_ring.set(fraction, "tour" if remaining is None else hms(remaining), brick,
                           color or MUTED)
        self.m_total.set(hms(s.total_remaining_s), f"écoulé : {hms(s.elapsed_s)}")
        self.total_bar.setValue(round(1000 * s.elapsed_s / s.total_s) if s.total_s else 0)
        self.m_cadence.set(f"{r.cadence_rpm:.0f}" if r and r.cadence_rpm is not None else "—", "tr/min")
        self._refresh_heart_rate(self.m_heart, s)
        self.intensity_label.setText(f"{s.intensity_pct} %")
        self.intensity_label.setStyleSheet(f"color: {TEXT if s.intensity_pct == 100 else ACCENT};")
        if s.state is State.FINISHED:
            self.current_label.setText("Séance terminée")
            self.next_label.setText("")
        else:
            self.current_label.setText(f"Maintenant : {describe_segment(s.segment, self.ftp, s.intensity_pct)}"
                                       f"   ·   {brick}")
            self.next_label.setText(f"Ensuite : {describe_segment(s.next_segment, self.ftp, s.intensity_pct)}"
                                    if s.next_segment else "Dernière brique")
        self.play_button.setText({State.RUNNING: "Pause", State.PAUSED: "Reprendre",
                                  State.FINISHED: "Recommencer"}.get(s.state, "Démarrer"))
        self.play_button.setIcon(icon("pause" if s.state is State.RUNNING else "play", "#18191c"))
        self.chart.update()

    def _refresh_source(self) -> None:
        source = self.source
        self._remember("trainer", source)
        text = self._display_name("trainer", source)
        color = GOOD
        if isinstance(source, Trainer) and (source.state is not SensorState.CONNECTED or source.latest() is None):
            text += f" : {source.status if source.state is not SensorState.CONNECTED else 'signal perdu'}"
            color = WAIT
        elif not isinstance(source, Trainer):
            color = OFF  # simulé
        if self.source_label.text() != text:
            self.source_label.setText(text)
        if getattr(self, "_source_color", None) != color:
            self._source_color = color
            self.source_label.setIcon(dot(color))

    def _refresh_heart_chip(self) -> None:
        sensor = self.heart_rate
        if sensor is None:
            text, color = "Cardio : aucun", OFF
        elif sensor.latest() is not None:
            name = self._display_name("hr", sensor)
            text, color = (name if "cardio" in name.lower() else f"Cardio · {name}"), GOOD
        else:
            text, color = f"Cardio : {sensor.status if sensor.state is not SensorState.CONNECTED else 'signal perdu'}", WAIT
        if self.heart_chip.text() != text:
            self.heart_chip.setText(text)
        if getattr(self, "_heart_color", None) != color:
            self._heart_color = color
            self.heart_chip.setIcon(dot(color))

    def _refresh_heart_rate(self, metric: Metric,
                            session: WorkoutSession | FreeRideSession | RouteSession) -> None:
        sensor = self.heart_rate
        if sensor is None:
            metric.set("Off", "aucun capteur · pastille Cardio en haut")
            return
        self._remember("hr", sensor)
        heart = sensor.latest()
        average = session.average_heart_rate()
        if heart is not None:
            sub = f"bpm · moy. {average:.0f}" if average else "bpm"
            if heart.battery_pct is not None and heart.battery_pct <= 20:
                sub += f" · batterie {heart.battery_pct} %"
            if heart.contact is False:
                sub += " · mauvais contact"
            metric.set(f"{heart.bpm}", sub)
        else:
            state = sensor.status if sensor.state is not SensorState.CONNECTED else "signal perdu"
            metric.set("Off", f"{self._display_name('hr', sensor)} : {state}")


FINISH_TIP = ("Terminer la sortie : choix du format (.fit par défaut), du nom et du dossier ; "
              "le .fit part vers Strava / Nolio (Ctrl+T)")


class _SyncSignals(QObject):
    done = Signal(list)  # comptes rendus d'envoi, émis depuis le fil d'envoi


class SensorDialog(QDialog):
    """Choix d'un appareil : un appareil mémorisé (renommable), simulé, Bluetooth (avec recherche)
    ou ANT+ (numéro, 0 = premier trouvé). Les appareils se mémorisent d'eux-mêmes à la connexion."""

    TITLE = ""
    ROLE = ""
    KINDS: list[tuple[str, str | None]] = []
    SIMULATED_TEXT = ""
    DEFAULT_KIND: str | None = "sim"  # proposé si rien n'a encore été choisi
    SCAN_TEXT = ""
    ANY_DEVICE = ""

    def __init__(self, book: DeviceBook, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.book = book
        self.setWindowTitle(self.TITLE)
        self.setMinimumWidth(460)
        form = QFormLayout(self)
        self.kind = QComboBox()
        self.pages = QStackedWidget()
        self._kind_pages: dict[str | None, QWidget] = {}
        self.pages.addWidget(self._saved_page())
        for device in book.devices(self.ROLE):
            self.kind.addItem(str(device), device)
        for label, kind in self.KINDS:
            self.kind.addItem(label, kind)
            page = self._kind_pages[kind] = self._page(kind)
            self.pages.addWidget(page)
        form.addRow("Appareil", self.kind)
        form.addRow("", self.pages)
        self.message = QLabel("")
        self.message.setObjectName("metricSub")
        self.message.setWordWrap(True)
        form.addRow("", self.message)
        self._extra_rows(form)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        self.kind.currentIndexChanged.connect(self._kind_changed)
        self.kind.setCurrentIndex(-1)
        self.kind.setCurrentIndex(self._initial_index())

        self._scan_result: list | Exception | None = None
        self._scan_timer = QTimer(self)
        self._scan_timer.timeout.connect(self._scan_done)

    def _extra_rows(self, form: QFormLayout) -> None:
        """Lignes propres à un type d'appareil, avant les boutons OK / Annuler."""

    def _initial_index(self) -> int:
        """Le dernier appareil choisi, sinon l'appareil par défaut (simulé, ou aucun cardio)."""
        last = self.book.last(self.ROLE)
        if last is not None:
            kind, ident = last
            saved = self.book.find(self.ROLE, kind, ident) if kind in ("ble", "ant") else None
            index = self.kind.findData(saved if saved is not None else kind)
            if index >= 0:
                if saved is None and kind == "ble" and ident:
                    self.ble_devices.addItem(ident, ident)
                    self.ble_devices.setCurrentIndex(self.ble_devices.count() - 1)
                elif saved is None and kind == "ant" and ident and ident.isdigit():
                    self.ant_number.setValue(int(ident))
                return index
        return self.kind.findData(self.DEFAULT_KIND)

    def _saved_page(self) -> QWidget:
        page = QWidget()
        row = QHBoxLayout(page)
        row.setContentsMargins(0, 0, 0, 0)
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("Nom de l'appareil")
        self.name_edit.setToolTip("Renommer cet appareil (Entrée pour valider)")
        self.name_edit.editingFinished.connect(self._rename)
        self.forget_button = QPushButton("Oublier")
        self.forget_button.setToolTip("Retirer cet appareil des appareils mémorisés")
        self.forget_button.clicked.connect(self._forget)
        row.addWidget(QLabel("Nom"))
        row.addWidget(self.name_edit, 1)
        row.addWidget(self.forget_button)
        return page

    def _page(self, kind: str | None) -> QWidget:
        if kind == "sim":
            return QLabel(self.SIMULATED_TEXT)
        if kind == "ble":
            page = QWidget()
            row = QHBoxLayout(page)
            row.setContentsMargins(0, 0, 0, 0)
            self.ble_devices = QComboBox()
            self.ble_devices.addItem(self.ANY_DEVICE, None)
            self.scan_button = QPushButton("Rechercher")
            self.scan_button.clicked.connect(self._scan)
            row.addWidget(self.ble_devices, 1)
            row.addWidget(self.scan_button)
            return page
        if kind == "ant":
            self.ant_number = QSpinBox()
            self.ant_number.setRange(0, 65535)
            self.ant_number.setSpecialValueText(self.ANY_DEVICE)
            return self.ant_number
        return QWidget()

    def saved_device(self) -> SavedDevice | None:
        data = self.kind.currentData()
        return data if isinstance(data, SavedDevice) else None

    def _kind_changed(self) -> None:
        saved = self.saved_device()
        if saved is not None:
            self.pages.setCurrentIndex(0)
            self.name_edit.setText(saved.name)
            what = "Adresse Bluetooth" if saved.kind == "ble" else "Numéro ANT+"
            self.message.setText(f"{what} : {saved.ident}")
        else:
            page = self._kind_pages.get(self.kind.currentData())
            if page is not None:
                self.pages.setCurrentWidget(page)
            self.message.setText("")

    def _rename(self) -> None:
        saved = self.saved_device()
        if saved is None or self.name_edit.text().strip() == saved.name:
            return
        self.book.rename(saved, self.name_edit.text())
        self.book.save()
        self.name_edit.setText(saved.name)
        self.kind.setItemText(self.kind.currentIndex(), str(saved))

    def _forget(self) -> None:
        saved = self.saved_device()
        if saved is None:
            return
        self.book.forget(saved)
        self.book.save()
        self.kind.removeItem(self.kind.currentIndex())
        self.kind.setCurrentIndex(self.kind.findData(saved.kind))
        if saved.kind == "ble":
            self.ble_devices.setCurrentIndex(0)
        self.message.setText(f"« {saved.name} » oublié.")

    def choice(self) -> tuple[str | None, str | None]:
        """Ce qui est choisi : (type, adresse ou numéro) ; type None = aucun appareil."""
        saved = self.saved_device()
        if saved is not None:
            return saved.kind, saved.ident
        kind = self.kind.currentData()
        if kind == "ble":
            return kind, self.ble_devices.currentData()
        if kind == "ant":
            return kind, ant_ident(self.ant_number.value())
        return kind, None

    def _connect_args(self) -> tuple[str | None, dict]:
        kind, ident = self.choice()
        if kind == "ant":
            return kind, {"address": None, "device_number": int(ident) if ident else 0}
        return kind, {"address": ident if kind == "ble" else None, "device_number": 0}

    def accept(self) -> None:
        self._rename()
        kind, ident = self.choice()
        self.book.set_last(self.ROLE, kind, ident)
        self.book.save()
        super().accept()

    def scan_devices(self) -> list:
        raise NotImplementedError

    def _scan(self) -> None:
        def work() -> None:
            try:
                self._scan_result = self.scan_devices()
            except Exception as e:  # noqa: BLE001 (affiché à l'utilisateur)
                self._scan_result = e

        self._scan_result = None
        self.scan_button.setEnabled(False)
        self.message.setText(self.SCAN_TEXT)
        threading.Thread(target=work, daemon=True).start()
        self._scan_timer.start(200)

    def _scan_done(self) -> None:
        result = self._scan_result
        if result is None:
            return
        self._scan_timer.stop()
        self.scan_button.setEnabled(True)
        if isinstance(result, Exception):
            self.message.setText(f"Recherche impossible : {result}")
            return
        while self.ble_devices.count() > 1:
            self.ble_devices.removeItem(1)
        for device in result:
            saved = self.book.display_name(self.ROLE, "ble", device.address)
            label = f"{saved}  ({device.address})" if saved else str(device)
            self.ble_devices.addItem(label, device.address)
        if result:
            self.ble_devices.setCurrentIndex(1)
        self.message.setText(f"{len(result)} appareil(s) trouvé(s)." if result else "Aucun appareil trouvé.")


class HeartRateDialog(SensorDialog):
    """Choix du capteur cardio : aucun, Bluetooth ou ANT+."""

    TITLE = "Capteur cardiaque"
    ROLE = "hr"
    KINDS = [("Aucun", None), ("Bluetooth", "ble"), ("ANT+ (clé USB)", "ant")]
    DEFAULT_KIND = None
    SCAN_TEXT = "Recherche des ceintures Bluetooth (5 s)… Mouillez la sangle pour la réveiller."
    ANY_DEVICE = "Première ceinture trouvée"

    def scan_devices(self) -> list:
        from ..sensors.ble import scan_heart_rate_monitors
        return scan_heart_rate_monitors(timeout=5)

    def sensor(self) -> BackgroundSensor[HeartRateReading] | None:
        kind, args = self._connect_args()
        if kind is None:
            return None
        return open_heart_rate_sensor(kind, **args)


class TrainerDialog(SensorDialog):
    """Choix du home trainer : simulé, en Bluetooth ou en ANT+ (toutes marques)."""

    TITLE = "Home trainer"
    ROLE = "trainer"
    KINDS = [("Simulé", "sim"), ("Bluetooth (FTMS)", "ble"), ("ANT+ FE-C (clé USB)", "ant")]
    SIMULATED_TEXT = "Puissance imitée : rejoint la consigne en quelques secondes."
    SCAN_TEXT = ("Recherche des home trainers Bluetooth (5 s)… Pédalez pour réveiller le home trainer, "
                 "et fermez les autres applis qui pourraient s'y connecter (Zwift, appli de la marque…).")
    ANY_DEVICE = "Premier home trainer trouvé"

    def __init__(self, book: DeviceBook, parent: QWidget | None = None, *, current: PowerSource | None = None,
                 current_name: str = "", before_calibration=None) -> None:
        self.current = current
        self.current_name = current_name or getattr(current, "name", "")
        self.before_calibration = before_calibration
        super().__init__(book, parent)

    def _extra_rows(self, form: QFormLayout) -> None:
        self.calibrate_button = QPushButton("Calibrer…")
        self.calibrate_button.setToolTip("Calibration (spindown) du home trainer en service, pas à pas")
        self.calibrate_button.clicked.connect(self.calibrate)
        usable = self.current is not None and can_calibrate(self.current)
        self.calibrate_button.setEnabled(usable)
        row = QWidget()
        line = QHBoxLayout(row)
        line.setContentsMargins(0, 0, 0, 0)
        line.addWidget(self.calibrate_button)
        hint = QLabel(f"{self.current_name}" if usable else "")
        hint.setObjectName("metricSub")
        line.addWidget(hint, 1)
        form.addRow("Calibration", row)

    def calibrate(self) -> None:
        if self.current is None:
            return
        if self.before_calibration is not None:
            self.before_calibration()
        CalibrationDialog(self.current, self.current_name, self).exec()

    def scan_devices(self) -> list:
        from ..sensors.trainer_ble import scan_trainers
        return scan_trainers(timeout=5)

    def sensor(self) -> PowerSource:
        kind, args = self._connect_args()
        return open_power_source(kind, **args)




def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="home-trainer-gui", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("file", nargs="?", help="séance .erg, .mrc, .zwo ou .fit, ou parcours .gpx")
    parser.add_argument("--profile", help="profil du cycliste (créé s'il n'existe pas) ; sinon choisi au lancement")
    parser.add_argument("--ftp", type=float, help="FTP en watts (remplace celle du profil)")
    parser.add_argument("--weight", type=float,
                        help="poids du cycliste en kg, pour la pente simulée (remplace celui du profil)")
    parser.add_argument("--bricks", help="séance en notation briques")
    parser.add_argument("--trainer", choices=["sim", "ble", "ant"],
                        help="home trainer : simulé, Bluetooth (FTMS) ou ANT+ (FE-C) "
                             "(défaut : le dernier utilisé, sinon simulé)")
    parser.add_argument("--trainer-address", help="adresse Bluetooth du home trainer (sinon le premier trouvé)")
    parser.add_argument("--trainer-ant-id", type=int, default=0,
                        help="numéro ANT+ du home trainer (sinon le premier trouvé)")
    parser.add_argument("--hr", choices=["ble", "ant", "aucun"],
                        help="capteur cardio : Bluetooth, ANT+ ou aucun "
                             "(défaut : le dernier utilisé, sinon aucun)")
    parser.add_argument("--hr-address", help="adresse Bluetooth de la ceinture (sinon la première trouvée)")
    parser.add_argument("--hr-ant-id", type=int, default=0,
                        help="numéro ANT+ de la ceinture (sinon la première trouvée)")
    args = parser.parse_args(argv)

    _set_windows_app_id()
    app = QApplication(sys.argv[:1])
    app.setApplicationName("Home trainer")
    install(app)  # polices embarquées et feuille de style
    if ICON.exists():
        app.setWindowIcon(QIcon(str(ICON)))
    # Sans option, on rebranche les appareils de la dernière fois (mémorisés dans le profil utilisateur).
    book = DeviceBook.load()
    profiles = ProfileBook.load()
    profiles.migrate_legacy()  # comptes et sorties d'avant les profils → premier profil
    if args.profile:
        profile = profiles.find(args.profile) or profiles.create(args.profile)
        profiles.last = profile.id
        profiles.save()
    else:
        profile = profiles.startup_profile() or choose_profile(profiles, startup=True)
    if profile is None:  # « Quitter » sur le choix du profil
        return 0
    if args.hr is None:
        hr_kind, hr_address, hr_number = book.startup_choice("hr", ("ble", "ant", None), default=None)
    else:
        hr_kind = None if args.hr == "aucun" else args.hr
        hr_address, hr_number = args.hr_address, args.hr_ant_id
    if args.trainer is None:
        trainer_kind, trainer_address, trainer_number = book.startup_choice("trainer", ("sim", "ble", "ant"))
    else:
        trainer_kind, trainer_address, trainer_number = args.trainer, args.trainer_address, args.trainer_ant_id
    heart_rate = (None if hr_kind is None
                  else open_heart_rate_sensor(hr_kind, address=hr_address, device_number=hr_number))
    source = open_power_source(trainer_kind, address=trainer_address, device_number=trainer_number)
    try:
        workout = parse_workout(args.bricks or DEFAULT_BRICKS,
                                name="Séance en briques" if args.bricks else "Sweet spot (démo)")
    except BrickSyntaxError as e:
        # Lancée depuis une icône, l'appli n'a pas de console : les erreurs s'affichent dans une fenêtre.
        QMessageBox.warning(None, "Briques invalides", str(e))
        workout = parse_workout(DEFAULT_BRICKS, name="Sweet spot (démo)")
    window = MainWindow(workout, profile.ftp, source=source, heart_rate=heart_rate, book=book,
                        profiles=profiles, profile=profile)
    window.resize(1240, 760)
    if args.ftp:
        window.ftp_box.setValue(int(args.ftp))
        window._ftp_changed()
    if args.weight:
        window.weight_box.setValue(args.weight)
    window.set_library(Library.load(documents=documents_folder()))
    window.show()
    if args.file:  # fichier passé en argument, ou glissé sur l'icône
        window.open_path(args.file)
    return app.exec()


def _set_windows_app_id() -> None:
    """Sous Windows, montre l'icône de l'appli dans la barre des tâches (et non celle de Python)."""
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("HomeTrainer.App")
        except (AttributeError, OSError):
            pass


if __name__ == "__main__":
    sys.exit(main())
