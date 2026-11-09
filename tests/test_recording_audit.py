"""Constat de l'étape 1 (sortie du 7 novembre 2026, comparée au Garmin) : défauts d'enregistrement,
corrigés à l'étape 1b.

Chaque test décrit le comportement attendu, avec l'avant et l'après en docstring quand un calcul change.
Un défaut constaté mais pas encore corrigé s'y ajoute marqué `xfail(strict=True)`, marque retirée à la
correction.
"""

import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")
fit_sdk = pytest.importorskip("garmin_fit_sdk")

import home_trainer.ui.session as session_module  # noqa: E402
from home_trainer.bricks import parse_workout  # noqa: E402
from home_trainer.formats.fit_activity import ActivityPoint, encode_activity, summarize  # noqa: E402
from home_trainer.sensors.trainer import CyclingPowerDecoder  # noqa: E402
from home_trainer.sync import AccountBook, Outbox  # noqa: E402
from home_trainer.ui.app import MainWindow  # noqa: E402

START = 1_762_534_558  # 7 novembre 2026, 16:55:58 UTC


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def window(app, tmp_path):
    w = MainWindow(parse_workout("2m@150"), 250, accounts=AccountBook(tmp_path / "comptes.json"),
                   outbox=Outbox(tmp_path / "sorties"))
    yield w
    w.close()


class FakeClock:
    """Temps réel simulé, lu comme un `QElapsedTimer` (millisecondes entières) et comme `time.time()`."""

    def __init__(self, start: float) -> None:
        self.start = start
        self.ns = 0  # temps réel écoulé, en nanosecondes
        self._mark = 0

    def advance_ms(self, ms: float) -> None:
        self.ns += round(ms * 1_000_000)

    def now(self) -> float:
        return self.start + self.ns / 1e9

    # API de QElapsedTimer utilisée ou utilisable par l'application
    def restart(self) -> int:
        elapsed, self._mark = self.ns - self._mark, self.ns
        return elapsed // 1_000_000  # comme Qt : millisecondes entières, tronquées

    def elapsed(self) -> int:
        return (self.ns - self._mark) // 1_000_000

    def nsecsElapsed(self) -> int:  # noqa: N802 (nom de l'API Qt)
        return self.ns - self._mark

    def start_timer(self) -> None:
        self._mark = self.ns


def ride_free(window, monkeypatch, ticks_ms, start=START + 0.4):
    """Mode libre lancé, puis un tic de l'application par durée de `ticks_ms`."""
    clock = FakeClock(start)
    monkeypatch.setattr(window, "clock", clock)
    monkeypatch.setattr(session_module, "time", SimpleNamespace(time=clock.now))
    window.enter_free_ride()
    window.free.start()
    clock.restart()
    for ms in ticks_ms:
        clock.advance_ms(ms)
        window._on_tick()
    return clock


def test_ride_clock_follows_real_time(window, monkeypatch):
    clock = ride_free(window, monkeypatch, [200.6] * 3000)  # 601,8 s réelles en tics de 200,6 ms
    real_s = clock.ns / 1e9
    assert window.free.elapsed_s == pytest.approx(real_s, abs=0.05)
    assert len(window.free.samples) == int(real_s)


def test_qelapsedtimer_restart_truncates_to_milliseconds(app):
    """La cause du défaut précédent, vue sur Qt lui-même."""
    from PySide6.QtCore import QElapsedTimer
    timer = QElapsedTimer()
    timer.start()
    assert isinstance(timer.restart(), int)


@pytest.mark.parametrize("ticks_ms", [
    pytest.param([190, 215, 205, 195, 210, 185, 200, 220, 180, 200] * 60, id="gigue"),
    pytest.param([200] * 50 + [2600] + [200] * 50, id="gel_de_l_interface"),
])
def test_recorded_seconds_are_consecutive(window, monkeypatch, ticks_ms):
    ride_free(window, monkeypatch, ticks_ms)
    seconds = [round(s.at) for s in window.free.samples]
    steps = {b - a for a, b in zip(seconds, seconds[1:])}
    assert steps == {1}
    assert [s.t for s in window.free.samples] == list(range(1, len(seconds) + 1))


def test_recorded_power_is_the_mean_of_the_second():
    """Capteur à 1 Hz lu à chaque tic de 200 ms : 210 W sur 0,4 s puis 150 W sur 0,6 s.
    Avant : la lecture du tic qui enregistre (150 ou 210 W) ; après : (210×0,4 + 150×0,6) / 1 = 174 W."""
    sampler = session_module.SecondSampler()
    out = []
    for i, power in enumerate([210, 210, 150, 150, 150]):
        out += sampler.feed(i * 0.2, (i + 1) * 0.2, power, cadence_rpm=90 if i < 2 else 80, speed_kmh=30)
    assert len(out) == 1
    assert out[0].t == 1
    assert out[0].power_w == pytest.approx(174.0)
    assert out[0].cadence_rpm == pytest.approx(84.0)
    assert out[0].speed_kmh == pytest.approx(30.0)


def test_a_tick_across_a_second_is_split():
    """Un tic de 0,5 s à cheval sur deux secondes compte pour moitié dans chacune."""
    sampler = session_module.SecondSampler()
    assert sampler.feed(0.0, 0.75, 100) == []
    first = sampler.feed(0.75, 1.25, 200)  # 0,25 s dans la 1re seconde, 0,25 s dans la 2e
    assert first[0].power_w == pytest.approx((100 * 0.75 + 200 * 0.25) / 1.0)
    second = sampler.feed(1.25, 2.0, 300)
    assert second[0].power_w == pytest.approx((200 * 0.25 + 300 * 0.75) / 1.0)


def test_mute_sensor_second_is_skipped():
    sampler = session_module.SecondSampler()
    assert [s.t for s in sampler.feed(0.0, 3.0, None)] == []
    assert [s.t for s in sampler.feed(3.0, 4.0, 150)] == [4]


def test_pause_shifts_the_clock_time_of_next_seconds(window, monkeypatch):
    """Pause de 30 s : les secondes de chrono restent consécutives, l'heure saute de 30 s."""
    clock = ride_free(window, monkeypatch, [200] * 26)  # 5,2 s
    window._toggle()  # pause
    for _ in range(150):  # 30 s en pause
        clock.advance_ms(200)
        window._on_tick()
    window._toggle()  # reprise
    for _ in range(25):
        clock.advance_ms(200)
        window._on_tick()
    samples = window.free.samples
    assert [s.t for s in samples] == list(range(1, 11))
    gaps = [round(b.at - a.at) for a, b in zip(samples, samples[1:])]
    assert gaps == [1, 1, 1, 1, 31, 1, 1, 1, 1]


def heart_rates_in_fit(points):
    stream = fit_sdk.Stream.from_byte_array(bytearray(encode_activity(points)))
    messages, errors = fit_sdk.Decoder(stream).read()
    assert not errors
    return [r.get("heart_rate") for r in messages["record_mesgs"]]


def test_zero_heart_rate_is_not_written(app):
    points = [ActivityPoint(at=START + i, power_w=150, cadence_rpm=90, heart_rate_bpm=0 if i == 5 else 140,
                            timer_s=float(i)) for i in range(10)]
    hearts = heart_rates_in_fit(points)
    assert hearts[5] is None and hearts[4] == 140


def crank_frame(power: int, revs: int, event_1024: int) -> bytes:
    """Trame Cycling Power avec tours de pédalier (drapeau 0x20)."""
    return ((0x20).to_bytes(2, "little") + power.to_bytes(2, "little", signed=True)
            + revs.to_bytes(2, "little") + event_1024.to_bytes(2, "little"))


def test_cadence_drops_soon_after_pedaling_stops():
    """Avant : 0 après 4 trames sans tour, soit 4 s à 1 Hz ; après : 0 dès 2 s sans tour (3e trame à 1 Hz)."""
    decoder = CyclingPowerDecoder()
    revs, event = 0, 0
    reading = None
    for t in range(10):  # 90 tr/min, une trame par seconde
        revs += 1
        event += round(1024 * 60 / 90)
        reading = decoder.feed(crank_frame(150, revs, event), now=float(t))
    assert reading is not None and reading.cadence_rpm == pytest.approx(90, abs=1)
    cadences = [decoder.feed(crank_frame(0, revs, event), now=9.0 + s).cadence_rpm for s in (1, 2, 3)]
    assert cadences[:2] == [pytest.approx(90, abs=1)] * 2  # une ou deux notifications perdues : pas de faux 0
    assert cadences[2] == 0


def test_slow_cadence_has_no_false_zero_at_4_hz():
    """Home trainer à 4 Hz, 30 tr/min (un tour toutes les 2 s) : l'ancienne règle affichait 0 entre deux tours."""
    decoder = CyclingPowerDecoder()
    cadences = []
    for n in range(80):  # 20 s
        t = n / 4
        revs = int(t / 2)
        cadences.append(decoder.feed(crank_frame(100, revs, revs * 2048), now=t).cadence_rpm)
    assert all(c == pytest.approx(30) for c in cadences[12:])


def timer_events(points):
    stream = fit_sdk.Stream.from_byte_array(bytearray(encode_activity(points)))
    messages, errors = fit_sdk.Decoder(stream).read()
    assert not errors
    return [e["event_type"] for e in messages["event_mesgs"] if e["event"] == "timer"]


def test_sensor_gap_keeps_ride_timer(app):
    """Avant : trou de 8 s compté comme une pause, chrono du .fit 20 s ; après : 27 s, sans pause."""
    times = list(range(10)) + list(range(17, 27))  # capteur muet 7 s, chrono qui continue
    points = [ActivityPoint(at=START + t, power_w=150, cadence_rpm=90, heart_rate_bpm=140, timer_s=float(t))
              for t in times]
    assert summarize(points).timer_s == pytest.approx(27.0)
    assert timer_events(points) == ["start", "stop_all"]


def test_real_pause_stays_a_pause(app):
    """Pause de 30 s (chrono arrêté) : pause dans le .fit, chrono de 20 s, heure écoulée de 50 s."""
    points = [ActivityPoint(at=START + i + (30 if i >= 10 else 0), power_w=150, timer_s=float(i + 1))
              for i in range(20)]
    summary = summarize(points)
    assert summary.timer_s == pytest.approx(20.0)
    assert summary.elapsed_s == pytest.approx(50.0)
    assert timer_events(points) == ["start", "stop", "start", "stop_all"]
