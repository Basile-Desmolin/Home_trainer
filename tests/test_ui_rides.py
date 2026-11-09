"""Fin de sortie dans la fenêtre : fenêtre d'enregistrement, .fit, anti-veille, comptes (Qt hors écran)."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")
QtGui = pytest.importorskip("PySide6.QtGui")
QtCore = pytest.importorskip("PySide6.QtCore")

from home_trainer.bricks import parse_workout  # noqa: E402
from home_trainer.sync import AccountBook, Outbox  # noqa: E402
from home_trainer.ui.accounts import AccountsDialog  # noqa: E402
from home_trainer.ui.app import MainWindow  # noqa: E402
from home_trainer.ui.ride_export import DISCARD, RideExport, RideExportDialog, clean_name  # noqa: E402
from home_trainer.ui.session import State  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def window(app, tmp_path):
    w = MainWindow(parse_workout("2m@150"), 250, accounts=AccountBook(tmp_path / "comptes.json"),
                   outbox=Outbox(tmp_path / "sorties"))
    w.asked = []

    def answer(parent, default, summary="", **kw):  # « Enregistrer » sans rien changer
        w.asked.append(default)
        return default

    w.ask_ride_export = answer
    w.confirmed = []
    w.confirm_stop = lambda parent, action, question: w.confirmed.append(action) or True
    yield w
    w.close()


def pedal(session, seconds, start=1_759_480_000):
    session.start()
    for i in range(seconds):
        session.record(150, 90, 130, 30.0, at=start + i)
        session.tick(1)


def test_finished_workout_is_saved_once(window):
    pedal(window.session, 120)
    assert window.session.state is State.FINISHED
    window._on_tick()
    window._on_tick()
    QtWidgets.QApplication.processEvents()  # la fenêtre d'enregistrement s'ouvre hors du tic
    window._on_tick()
    QtWidgets.QApplication.processEvents()
    assert len(window.asked) == 1
    files = list(window.outbox.dir.glob("*.fit"))
    assert len(files) == 1
    entry = window.outbox.entries()[files[0].name]
    assert entry["title"] == window.session.workout.name


def test_continue_in_free_ride_after_a_workout_goes_in_the_same_file(window):
    """Étape 11d : en fin de séance, « Continuer en mode libre » : la suite démarre en ERG à la
    puissance de la dernière brique ; « Terminer » enregistre un seul .fit (séance 2:00, puis mode
    libre 0:30 dans un tour à part), au nom de la séance."""
    from home_trainer.ui.ride_export import CONTINUE
    from home_trainer.ui.session import FreeMode
    fit_sdk = pytest.importorskip("garmin_fit_sdk")
    asked = []
    window.ask_ride_export = lambda parent, default, summary="", **kw: asked.append(kw) or CONTINUE
    pedal(window.session, 120)
    window._on_tick()
    QtWidgets.QApplication.processEvents()  # la fenêtre d'enregistrement s'ouvre hors du tic
    assert asked == [{"can_continue": True}]
    assert window.free_ride and window.free.state is State.RUNNING and window.free.mode is FreeMode.ERG
    assert window.free.target_w == 150 and window.free.samples is window.session.samples
    assert window.free.elapsed_s == 120 and not list(window.outbox.dir.glob("*.fit"))
    window.free.pause()
    pedal(window.free, 30, start=1_759_480_120)
    window.ask_ride_export = lambda parent, default, summary="", **kw: asked.append(kw) or default
    window.free_panel.finish_button.click()
    assert asked[-1] == {}  # plus de « continuer » une fois en mode libre
    [entry] = window.outbox.entries().values()
    assert entry["title"] == window.session.workout.name
    assert entry["description"].endswith(", puis mode libre (ERG).")
    [path] = window.outbox.dir.glob("*.fit")
    msgs, errors = fit_sdk.Decoder(fit_sdk.Stream.from_byte_array(bytearray(path.read_bytes()))).read()
    assert errors == [] and [lap["total_timer_time"] for lap in msgs["lap_mesgs"]] == [120, 30]
    window._on_tick()  # la séance finie n'est pas enregistrée une seconde fois
    QtWidgets.QApplication.processEvents()
    assert len(list(window.outbox.dir.glob("*.fit"))) == 1


def test_window_scale_follows_the_window_size():
    """Étape 11f : facteur 1 jusqu'à 1 280 × 800, puis selon le côté le moins agrandi, 1,8 au plus."""
    from home_trainer.ui.theme import window_scale
    assert window_scale(1280, 800) == 1.0 and window_scale(1000, 700) == 1.0
    assert window_scale(1920, 1080) == 1.35  # 1,5 en largeur, 1,35 en hauteur
    assert window_scale(2560, 1440) == 1.8 and window_scale(3840, 2160) == 1.8


def test_numbers_grow_with_the_window_and_full_screen(window):
    window.resize(1280, 800)
    window._apply_scale()
    power = window.cards["power"]
    size = power.value.font().pointSize()
    ring = window.step_ring.width()
    window.resize(1920, 1080)
    window._apply_scale()
    assert window.scale == 1.35
    assert power.value.font().pointSize() == round(size * 1.35)
    assert window.step_ring.width() == round(124 * 1.35)
    assert "font-size: 19px" in power.sub.styleSheet()  # 14 px × 1,35
    assert window.free_panel.grid.cards["power"].scale == 1.35
    window.workout_grid.choose(3, "elapsed")  # carte recréée : à la même échelle
    assert window.cards["elapsed"].scale == 1.35
    window.resize(1280, 800)
    window._apply_scale()
    assert power.value.font().pointSize() == size and window.step_ring.width() == ring
    assert power.sub.styleSheet() == ""
    window.toggle_full_screen()
    assert window.isFullScreen()
    QtWidgets.QApplication.sendEvent(window, QtGui.QKeyEvent(QtGui.QKeyEvent.KeyPress, QtCore.Qt.Key_Escape,
                                                             QtCore.Qt.NoModifier))
    assert not window.isFullScreen()


def test_p_switches_free_ride_between_erg_and_slope(window):
    from home_trainer.ui.session import FreeMode
    window.toggle_free_mode()  # en séance : rien
    assert window.free.mode is FreeMode.ERG
    window.enter_free_ride()
    window.toggle_free_mode()
    assert window.free.mode is FreeMode.SLOPE
    window.toggle_free_mode()
    assert window.free.mode is FreeMode.ERG


def test_finish_button_saves_free_ride_and_resets(window):
    window.enter_free_ride()
    pedal(window.free, 90)
    old = window.free
    window.free_panel.finish_button.click()
    assert window.free is not old and window.free.samples == []
    [entry] = window.outbox.entries().values()
    assert entry["title"] == "Mode libre"


def test_short_ride_is_saved(window):
    pedal(window.session, 30)
    window.finish_button.click()
    assert len(list(window.outbox.dir.glob("*.fit"))) == 1
    assert window.session.samples == []


def test_ride_without_pedaling_is_not_saved(window):
    window.finish_button.click()
    assert not window.outbox.dir.exists() or not list(window.outbox.dir.glob("*.fit"))


def test_speed_is_computed_by_the_application(window):
    from home_trainer.ui.power import Reading
    window.road_kmh = 31.5  # vitesse calculée (étape 7), avec ou sans vitesse du home trainer
    assert window._speed(Reading(200, 90)) == 31.5
    assert window._speed(Reading(200, 90, 25.0)) == 31.5


def test_accounts_dialog_keeps_credentials(window):
    dialog = AccountsDialog(window.accounts, window.outbox, window)
    box = dialog.boxes["strava"]
    box.client_id.setText(" 12345 ")
    box.client_secret.setText("abc")
    dialog.reject()
    again = AccountBook.load(window.accounts.path)
    assert again["strava"].client_id == "12345" and not again["strava"].connected
    assert dialog.boxes["nolio"].connect_button.text() == "Se connecter"


def test_finish_button_saves_route_ride(window):
    from home_trainer.route import Route
    route = Route.from_coordinates("Col test", [(45.0, 6.0, 500.0), (45.01, 6.0, 600.0), (45.02, 6.0, 650.0)])
    window.ride_route(route)
    pedal(window.route, 90)
    window.route_panel.finish_button.click()
    [entry] = window.outbox.entries().values()
    assert entry["title"] == "Col test" and "arrêté avant l'arrivée" in entry["description"]
    assert window.route.samples == []


def test_one_second_ride_is_saved(window):
    pedal(window.session, 1)
    window.finish_button.click()
    assert len(list(window.outbox.dir.glob("*.fit"))) == 1


def test_finish_asks_format_name_and_folder(window, tmp_path):
    pedal(window.session, 30)
    elsewhere = tmp_path / "ailleurs"
    window.ask_ride_export = lambda parent, default, summary="", **kw: RideExport(elsewhere, "Ma sortie", "fit")
    window.finish_button.click()
    assert (elsewhere / "Ma sortie.fit").exists()
    assert not list(window.outbox.dir.glob("*.fit"))
    # Le .fit choisi est celui qui part vers Strava / Nolio.
    assert window.outbox.pending(["strava"]) == [(str((elsewhere / "Ma sortie.fit").absolute()), "strava")]
    assert window._export_folder == elsewhere  # proposé la fois suivante


def test_other_format_keeps_a_fit_copy_for_upload(window, tmp_path):
    pedal(window.session, 30)
    window.ask_ride_export = lambda parent, default, summary="", **kw: RideExport(tmp_path, "sortie", "tcx")
    window.finish_button.click()
    assert (tmp_path / "sortie.tcx").read_text(encoding="utf-8").startswith("<?xml")
    assert len(list(window.outbox.dir.glob("*.fit"))) == 1
    assert window._export_fmt == "tcx"


def test_cancel_goes_back_to_the_ride(window):
    pedal(window.session, 30)
    window.ask_ride_export = lambda parent, default, summary="", **kw: None
    window.finish_button.click()
    assert len(window.session.samples) == 30 and window.session.state is State.PAUSED
    assert not window.outbox.dir.exists() or not list(window.outbox.dir.glob("*.fit"))


def test_discard_drops_the_ride(window):
    pedal(window.session, 30)
    window.ask_ride_export = lambda parent, default, summary="", **kw: DISCARD
    window.finish_button.click()
    assert window.session.samples == []
    assert not window.outbox.dir.exists() or not list(window.outbox.dir.glob("*.fit"))


def test_default_choice_is_fit_in_rides_folder(window):
    pedal(window.session, 30)
    window.finish_button.click()
    [default] = window.asked
    assert default.fmt == "fit" and default.folder == window.outbox.dir
    assert default.name.startswith("20")  # « 2026-10-05_1800_… », comme avant


def test_export_dialog_fields(app, tmp_path):
    dialog = RideExportDialog(RideExport(tmp_path, "2026-10-05_1800_Sweet-spot"), "Sweet spot · 1:00:00")
    assert dialog.format_box.currentData() == "fit"
    dialog.name_edit.setText(" Sortie: du soir.tcx ")
    dialog.format_box.setCurrentIndex(dialog.format_box.findData("csv"))
    assert dialog.current() == RideExport(tmp_path, "Sortie- du soir", "csv")
    dialog._save()
    assert dialog.choice == RideExport(tmp_path, "Sortie- du soir", "csv")
    assert clean_name("a/b?.fit") == "a-b-"


def test_pc_stays_awake_during_a_ride(window):
    calls = []
    window.keep_awake._call = lambda flags: calls.append(flags) or 1
    window._on_tick()
    assert not window.keep_awake.active
    window.session.start()
    window._on_tick()
    assert window.keep_awake.active
    window.session.record(150, 90)
    window.session.pause()
    window._on_tick()
    assert window.keep_awake.active  # en pause au milieu de la sortie : toujours pas de veille
    window.ask_ride_export = lambda parent, default, summary="", **kw: DISCARD
    window.finish_button.click()
    window._on_tick()
    assert not window.keep_awake.active
    assert calls == [0x80000003, 0x80000000]


def test_chart_draws_cadence(window):
    from home_trainer.ui.chart import CADENCE, WorkoutChart
    pedal(window.session, 60)

    def cadence_pixels(chart):
        chart.resize(800, 300)
        image = chart.grab().toImage()
        target = QtGui.QColor(CADENCE).hue()

        def lime(c):  # la courbe est lissée : sa teinte, pas forcément sa couleur exacte
            return c.saturation() > 60 and c.value() > 80 and abs(c.hue() - target) < 12

        return sum(1 for x in range(image.width()) for y in range(image.height()) if lime(image.pixelColor(x, y)))

    chart = WorkoutChart()
    chart.set_session(window.session)
    with_cadence = cadence_pixels(chart)
    from dataclasses import replace
    window.session.samples[:] = [replace(x, cadence_rpm=None) for x in window.session.samples]
    assert with_cadence > 100 and cadence_pixels(chart) == 0


def test_failed_save_keeps_the_ride(window, tmp_path, monkeypatch):
    pedal(window.session, 30)
    blocker = tmp_path / "fichier"
    blocker.write_text("")  # un fichier là où devrait être le dossier
    window.ask_ride_export = lambda parent, default, summary="", **kw: RideExport(blocker, "sortie", "tcx")
    monkeypatch.setattr(QtWidgets.QMessageBox, "warning", lambda *a, **k: None)
    window.finish_button.click()
    assert len(window.session.samples) == 30 and not window.session.exported


def test_nolio_redirect_field_and_cancel(window, monkeypatch):
    import home_trainer.ui.accounts as accounts
    opened = []
    monkeypatch.setattr(accounts.webbrowser, "open", opened.append)
    dialog = AccountsDialog(window.accounts, window.outbox, window)
    box = dialog.boxes["nolio"]
    assert box.redirect.placeholderText() == "http://localhost:8765/nolio"
    assert dialog.boxes["strava"].redirect is None
    box.client_id.setText("id")
    box.client_secret.setText("sec")
    box.redirect.setText("http://localhost:18769/nolio")
    box.connect_button.click()
    assert box.busy and box.connect_button.text() == "Annuler"
    box.connect_button.click()  # Annuler
    for _ in range(50):
        QtWidgets.QApplication.processEvents()
        if not box.busy:
            break
        import time
        time.sleep(0.1)
    assert not box.busy and box.connect_button.text() == "Se connecter"
    assert "redirect_uri=http%3A%2F%2Flocalhost%3A18769%2Fnolio" in opened[0]
    assert AccountBook.load(window.accounts.path)["nolio"].redirect_uri == "http://localhost:18769/nolio"


def test_finish_shows_the_summary_and_fills_the_history(window):
    from home_trainer.ride_stats import RideSummary
    seen = []
    window.ask_ride_export = lambda parent, default, summary="", **kw: seen.append(summary) or default
    pedal(window.session, 30)
    window.finish_button.click()
    [summary] = seen
    assert isinstance(summary, RideSummary)
    assert summary.duration_s == 30 and summary.avg_power_w == 150 and summary.avg_hr_bpm == 130
    assert summary.records == {}  # première sortie
    [ride] = window.ride_history().rides
    assert ride.file.endswith(".fit") and ride.title == window.session.workout.name

    seen.clear()
    window.session.start()
    for i in range(30):
        window.session.record(200, 90, 140, 32.0, at=1_759_490_000 + i)
        window.session.tick(1)
    window.finish_button.click()
    assert seen[0].records[5] == 150  # 200 W bat les 150 W de la première sortie
    assert len(window.ride_history().rides) == 2


def test_history_problems_are_shown(window, monkeypatch):
    """Historique illisible ou impossible à écrire : la sortie est enregistrée, et on le dit."""
    import home_trainer.history as history
    warnings = []
    monkeypatch.setattr(QtWidgets.QMessageBox, "warning", lambda parent, title, text: warnings.append(text))
    window.outbox.dir.mkdir(parents=True)
    (window.outbox.dir / history.FILE_NAME).write_text("abîmé", encoding="utf-8")
    pedal(window.session, 30)
    window.finish_button.click()
    assert "Historique illisible" in warnings[0] and "illisible-" in warnings[0]
    assert len(window.ride_history().rides) == 1

    warnings.clear()
    monkeypatch.setattr(history.RideHistory, "save", lambda self: (_ for _ in ()).throw(OSError("verrouillé")))
    pedal(window.session, 30, start=1_759_490_000)
    window.finish_button.click()
    assert "historique n'a pas pu être écrit : verrouillé" in warnings[0]
    assert len(list(window.outbox.dir.glob("*.fit"))) == 2  # le .fit, lui, est là


def test_summary_shows_heart_rate_zones_with_the_profile_max(app):
    """Étape 11g : bilan avec la FC max du profil : % de FC max sous la FC moyenne, temps par zone de FC."""
    from home_trainer.ride_stats import RideSummary
    from home_trainer.ui.ride_summary import SummaryPanel
    ride = RideSummary(1_759_480_000, "FC", 190, 250, 200, 200, 200, 38, avg_hr_bpm=27_350 / 180,
                       max_hr_bpm=185, hr_max=190, hr_zone_s=[60, 60, 30, 20, 10])
    panel = SummaryPanel(ride)
    assert panel.tiles["FC MOY."].sub.text() == "80 % FC max · max 97 %"
    assert panel.tiles["FC MAX"].value.text() == "185"
    assert panel.heart_zones is not None and panel.heart_zones.zone_s == [60, 60, 30, 20, 10]
    assert panel.tiles["CALORIES"].value.text() == "38"


def test_discarded_ride_stays_out_of_the_history(window):
    window.ask_ride_export = lambda parent, default, summary="", **kw: DISCARD
    pedal(window.session, 30)
    window.finish_button.click()
    assert window.ride_history().rides == []


def test_summary_and_history_windows(window):
    from home_trainer.ui.history import HistoryDialog
    pedal(window.session, 30)
    window.finish_button.click()
    history = window.ride_history()
    dialog = RideExportDialog(RideExport(window.outbox.dir, "x"), history.rides[0])
    assert dialog.panel is not None and dialog.panel.tiles["PUISSANCE MOY."].value.text() == "150"
    assert dialog.panel.tiles["PUISSANCE MOY."].unit.text() == "W"
    tiles = dialog.panel.tiles
    # 30 s à 150 W : 4,5 kJ ≈ 4,5 kcal, affichées arrondies.
    assert history.rides[0].calories_kcal == pytest.approx(4.5)
    assert tiles["CALORIES"].value.text() in ("4", "5") and tiles["CALORIES"].unit.text() == "kcal"
    assert dialog.panel.heart_zones is None  # sans FC max dans le profil
    hist = HistoryDialog(history, "Moi")
    assert hist.tree.topLevelItemCount() == 1
    assert hist.week_tile.value.text() != "—"
    hist.resize(960, 700)
    hist.grab()  # dessine les graphiques
    hist.tree.setCurrentItem(hist.tree.topLevelItem(0))
    assert hist.open_button.isEnabled()


def test_smooth_keeps_gaps_and_steadies_heart_rate():
    from home_trainer.ui.chart import smooth
    # Ceinture qui oscille d'un battement autour de 122 : la courbe lissée reste à 122.
    jitter = [(t, 122 + (-1, 0, 1, 0)[t % 4]) for t in range(60)]
    assert all(abs(v - 122) <= 0.3 for _, v in smooth(jitter, 10)[5:-5])
    # Un trou du capteur reste un trou, et on ne moyenne pas par-dessus.
    points = [(0, 100.0), (1, 100.0), (2, None), (3, 200.0), (4, 200.0)]
    assert smooth(points, 10) == [(0, 100.0), (1, 100.0), (2, None), (3, 200.0), (4, 200.0)]
    assert smooth([], 10) == []


def test_trace_draws_one_point_per_pixel():
    from home_trainer.ui.chart import trace
    points = [(t / 4, 120.0 + t % 2) for t in range(400)]  # 4 mesures par pixel
    lines = trace(points, lambda t: t, lambda v: v)
    assert len(lines) == 1 and len(lines[0]) == 100
    assert all(abs(q.y() - 120.5) < 1e-9 for q in lines[0])
    assert [q.x() for q in lines[0][:2]] == [0.375, 1.375]  # moyenne des 4 mesures du pixel
    # Un trou du capteur coupe la courbe : deux morceaux, rien ne relie 10 à 30.
    gap = trace([(0, 1.0), (1, 1.0), (2, None), (3, 1.0), (4, 1.0)], lambda t: t * 10, lambda v: v)
    assert [[q.x() for q in line] for line in gap] == [[0, 10], [30, 40]]
    assert trace([(0, None)], lambda t: t, lambda v: v) == []


def test_stroke_draws_the_same_curve_as_a_single_path(app):
    """Étape 9 : les segments séparés (bouts arrondis) donnent le même dessin qu'un chemin d'un tenant."""
    import math

    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen

    from home_trainer.ui.chart import stroke
    line = [QPointF(5 + i * 3, 50 + 30 * math.sin(i / 3)) for i in range(60)]
    images = []
    for draw in ("path", "segments"):
        img = QImage(200, 100, QImage.Format_ARGB32)
        img.fill(Qt.black)
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing)
        pen = QPen(QColor("#ffffff"), 2)
        if draw == "path":
            pen.setJoinStyle(Qt.RoundJoin)
            pen.setCapStyle(Qt.RoundCap)
            path = QPainterPath(line[0])
            for q in line[1:]:
                path.lineTo(q)
            p.setPen(pen)
            p.drawPath(path)
        else:
            stroke(p, pen, [line])
        p.end()
        images.append(img)
    path, segments = ([QColor(img.pixel(x, y)).red() for x in range(200) for y in range(100)] for img in images)
    lit = sum(a > 128 for a in path)
    # Même empreinte : aucun pixel allumé d'un côté et éteint de l'autre.
    assert lit > 300 and not any((a > 128 and b < 32) or (b > 128 and a < 32) for a, b in zip(path, segments))
    # Aux jonctions, les bords anticrénelés se superposent : à peine plus d'encre (mesuré : +2,5 %).
    assert abs(sum(segments) - sum(path)) <= 0.05 * sum(path)


def test_stopping_mid_ride_asks_first_and_cancel_keeps_riding(window):
    window.session.start()
    window.session.tick(5)
    window.confirm_stop = lambda parent, action, question: window.confirmed.append(action) and False
    window.finish_button.click()
    window.reset_button.click()
    assert window.confirmed == ["Terminer", "Recommencer"]
    assert window.session.state is State.RUNNING and window.session.elapsed_s == 5
    assert window.asked == []


def test_restart_resets_every_mode_once_confirmed(window):
    window.session.start()
    window.session.tick(5)
    window.reset_button.click()
    assert window.session.state is State.READY and window.confirmed == ["Recommencer"]
    window.enter_free_ride()
    window.free.start()
    window.free.pause()
    window.free_panel.reset_button.click()
    assert window.free.state is State.READY and window.confirmed[-1] == "Recommencer"


def test_space_after_the_end_asks_before_starting_again(window):
    """Étape 11a : séance finie, Espace ne relance plus une séance sans confirmation ; carte cible et
    anneau disent que la séance est finie."""
    pedal(window.session, 120)
    window.save_ride = lambda session: True
    window._refresh()
    assert window.m_target.value.text() == "fin" and window.step_ring.fraction == 0.0
    assert window.step_ring.text == "fin"
    window.confirm_stop = lambda parent, action, question: window.confirmed.append(action) or False
    window._space()
    assert window.session.state is State.FINISHED and window.confirmed == ["Recommencer"]
    window.confirm_stop = lambda parent, action, question: window.confirmed.append(action) or True
    window._space()
    assert window.session.state is State.RUNNING and window.session.elapsed_s == 0
    window._space()  # en pleine séance : pause, sans question
    assert window.session.state is State.PAUSED and window.confirmed == ["Recommencer", "Recommencer"]


def test_nothing_asked_before_the_ride_starts(window):
    window.reset_button.click()
    window.finish_button.click()
    window.enter_free_ride()
    window.free_panel.reset_button.click()
    assert window.confirmed == []


def test_virtual_gears_change_the_resistance_off_erg(window):
    from home_trainer.sensors import Slope
    from home_trainer.ui.session import FreeMode
    window.enter_free_ride()
    window.set_free_mode(FreeMode.ERG)
    window.free.start()
    window._push_target()
    window.shift_gear(+1)  # en ERG, le braquet ne compte pas
    assert window.gears.gear == 12 and window.source._target == window.free.target_w
    window.set_free_mode(FreeMode.SLOPE)
    window.shift_gear(+1)
    window.shift_gear(+1)
    assert window.gears.gear == 14
    target = window.source._target
    assert isinstance(target, Slope) and target.gear == pytest.approx(1.085 ** 2)
    assert "braquet 14 / 24" in window.free_panel.target_sub.text()
    window.leave_free_ride()
    window.toggle_erg()  # séance ERG off : le braquet suit
    window.session.start()
    window._push_target()
    assert window.source._target.gear == pytest.approx(1.085 ** 2)
    assert "braquet 14 / 24" in window.cards["cadence"].sub.text()
    window.toggle_erg()


def test_click_presses_shift_gears(window):
    from home_trainer.sensors.zwift_click import BleZwiftClick

    class FakeClick(BleZwiftClick):
        def start(self) -> None:  # pas de Bluetooth dans les tests
            pass

    click = FakeClick("AA:BB")
    window.set_click(click)
    window.enter_free_ride()
    from home_trainer.ui.session import FreeMode
    window.set_free_mode(FreeMode.SLOPE)
    click._on_shifts([-1, -1, -1], None)
    window._on_tick()
    assert window.gears.gear == 9
    assert window.click_chip_action.isVisible() or not window.isVisible()
    window.set_click(None)
