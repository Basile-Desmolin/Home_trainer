import pytest

from home_trainer.bricks import parse_workout
from home_trainer.ui import FreeRideSession, SimulatedTrainer, State, WorkoutSession


def make(text="1m@100 2x(30s@200 30s@100%) open@150", ftp=200):
    return WorkoutSession(parse_workout(text), ftp)


def test_ready_then_runs_only_when_started():
    s = make()
    s.tick(10)
    assert s.state is State.READY and s.elapsed_s == 0
    s.start()
    s.tick(10)
    assert s.elapsed_s == 10 and s.step_remaining_s == 50
    assert s.target_w == 100


def test_crosses_bricks_and_reports_remaining():
    s = make()
    s.start()
    s.tick(75)  # 60 s dans la 1re brique, 15 s dans la 2e
    assert s.index == 1 and s.step_remaining_s == 15
    assert s.target_w == 200
    assert s.total_remaining_s == s.total_s - 75
    s.tick(30)
    assert s.index == 2 and s.target_w == 200  # 100 % de 200 W de FTP


def test_open_brick_waits_for_lap():
    s = make()
    s.start()
    s.tick(10_000)
    assert s.index == 5 and s.step_remaining_s is None and s.state is State.RUNNING
    s.next_step()
    assert s.state is State.FINISHED and s.target_w is None


def test_pause_stops_the_clock():
    s = make()
    s.start()
    s.tick(5)
    s.toggle()
    s.tick(30)
    assert s.state is State.PAUSED and s.elapsed_s == 5


@pytest.mark.parametrize("deltas,expected", [([+1], 101), ([-1, -1], 98), ([+1] * 150, 200), ([-1] * 150, 1)])
def test_intensity_by_one_percent(deltas, expected):
    s = make()
    for d in deltas:
        s.adjust_intensity(d)
    assert s.intensity_pct == expected
    assert s.target_w == pytest.approx(100 * expected / 100)
    assert s.base_target_w == 100


def test_ramp_target_follows_time():
    s = make("10m@100>200")
    s.start()
    s.tick(300)
    assert s.target_w == pytest.approx(150)


def test_simulated_trainer_converges_to_target():
    t = SimulatedTrainer(noise_w=0, seed=1)
    t.set_target(250)
    for _ in range(100):
        r = t.read(0.2)
    assert r.power_w == pytest.approx(250, abs=2)
    t.set_target(0)
    assert t.read(0.2).power_w == 0


def test_free_ride_starts_near_60_percent_ftp_in_5_w_steps():
    s = FreeRideSession(ftp=250)
    assert s.target_w == 150 and s.target_pct == 60
    assert FreeRideSession(ftp=233).target_w == 140  # 139,8 W arrondi au multiple de 5


def test_free_ride_adjusts_by_5_w_within_bounds():
    s = FreeRideSession(ftp=200, target_w=100)
    assert s.adjust_target(+5) == 105
    assert s.adjust_target(-25) == 80
    assert s.set_target(153) == 155
    s.adjust_target(-10_000)
    assert s.target_w == 25
    s.adjust_target(+10_000)
    assert s.target_w == 1500


def test_free_ride_clock_and_samples_keep_the_target_of_the_moment():
    s = FreeRideSession(ftp=200, target_w=150)
    s.tick(10)
    assert s.state is State.READY and s.elapsed_s == 0
    s.start()
    s.tick(1)
    s.record(148, 90, 120)
    s.adjust_target(+50)
    s.tick(1)
    s.record(196, 92, 130)
    assert [(x.t, x.target_w) for x in s.samples] == [(1, 150), (2, 200)]
    assert s.average_power() == 172 and s.average_heart_rate() == 125
    s.toggle()
    s.tick(30)
    assert s.state is State.PAUSED and s.elapsed_s == 2
