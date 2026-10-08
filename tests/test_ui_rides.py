"""Fin de sortie dans la fenêtre : fenêtre d'enregistrement, .fit, anti-veille, comptes (Qt hors écran)."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")
QtGui = pytest.importorskip("PySide6.QtGui")

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

    def answer(parent, default, summary=""):  # « Enregistrer » sans rien changer
        w.asked.append(default)
        return default

    w.ask_ride_export = answer
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


def test_speed_is_estimated_without_trainer_speed(window):
    from home_trainer.ui.power import Reading
    assert 30 < window._speed(Reading(200, 90)) < 40
    assert window._speed(Reading(200, 90, 25.0)) == 25.0


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
    window.ask_ride_export = lambda parent, default, summary="": RideExport(elsewhere, "Ma sortie", "fit")
    window.finish_button.click()
    assert (elsewhere / "Ma sortie.fit").exists()
    assert not list(window.outbox.dir.glob("*.fit"))
    # Le .fit choisi est celui qui part vers Strava / Nolio.
    assert window.outbox.pending(["strava"]) == [(str((elsewhere / "Ma sortie.fit").absolute()), "strava")]
    assert window._export_folder == elsewhere  # proposé la fois suivante


def test_other_format_keeps_a_fit_copy_for_upload(window, tmp_path):
    pedal(window.session, 30)
    window.ask_ride_export = lambda parent, default, summary="": RideExport(tmp_path, "sortie", "tcx")
    window.finish_button.click()
    assert (tmp_path / "sortie.tcx").read_text(encoding="utf-8").startswith("<?xml")
    assert len(list(window.outbox.dir.glob("*.fit"))) == 1
    assert window._export_fmt == "tcx"


def test_cancel_goes_back_to_the_ride(window):
    pedal(window.session, 30)
    window.ask_ride_export = lambda parent, default, summary="": None
    window.finish_button.click()
    assert len(window.session.samples) == 30 and window.session.state is State.PAUSED
    assert not window.outbox.dir.exists() or not list(window.outbox.dir.glob("*.fit"))


def test_discard_drops_the_ride(window):
    pedal(window.session, 30)
    window.ask_ride_export = lambda parent, default, summary="": DISCARD
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
    window.ask_ride_export = lambda parent, default, summary="": DISCARD
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
    window.ask_ride_export = lambda parent, default, summary="": RideExport(blocker, "sortie", "tcx")
    monkeypatch.setattr(QtWidgets.QMessageBox, "warning", lambda *a, **k: None)
    window.finish_button.click()
    assert len(window.session.samples) == 30 and not window.session.exported


def test_finish_shows_the_summary_and_fills_the_history(window):
    from home_trainer.ride_stats import RideSummary
    seen = []
    window.ask_ride_export = lambda parent, default, summary="": seen.append(summary) or default
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


def test_discarded_ride_stays_out_of_the_history(window):
    window.ask_ride_export = lambda parent, default, summary="": DISCARD
    pedal(window.session, 30)
    window.finish_button.click()
    assert window.ride_history().rides == []


def test_summary_and_history_windows(window):
    from home_trainer.ui.history import HistoryDialog
    pedal(window.session, 30)
    window.finish_button.click()
    history = window.ride_history()
    dialog = RideExportDialog(RideExport(window.outbox.dir, "x"), history.rides[0])
    assert dialog.panel is not None and dialog.panel.tiles["PUISSANCE MOY."].value.text() == "150 W"
    hist = HistoryDialog(history, "Moi")
    assert hist.tree.topLevelItemCount() == 1
    assert hist.week_tile.value.text() != "—"
    hist.resize(960, 700)
    hist.grab()  # dessine les graphiques
    hist.tree.setCurrentItem(hist.tree.topLevelItem(0))
    assert hist.open_button.isEnabled()
