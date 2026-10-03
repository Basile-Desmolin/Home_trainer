"""Sorties en .fit d'activité et envoi vers Strava / Nolio (réseau simulé)."""

import base64
import json
import threading
import time
import urllib.request
from urllib.parse import parse_qs, urlparse

import pytest
from garmin_fit_sdk import Decoder, Stream

from home_trainer.bricks import parse_workout
from home_trainer.formats.fit_activity import ActivityPoint, activity_file_name, encode_activity, summarize
from home_trainer.sync import AccountBook, Nolio, Outbox, Strava, SyncError, connect, send_pending
from home_trainer.sync.web import Response, multipart, wait_for_code
from home_trainer.ui.session import FreeMode, FreeRideSession, State, WorkoutSession, ride_points, ride_title

T0 = 1_759_480_000  # 3 octobre 2025, 8 h 26 UTC


def decode(data: bytes) -> dict:
    assert Decoder(Stream.from_byte_array(bytearray(data))).check_integrity()
    messages, errors = Decoder(Stream.from_byte_array(bytearray(data))).read()
    assert errors == []
    return messages


def ride(n=120, start=T0, **kw):
    return [ActivityPoint(start + i, 200, 90, 140, 36.0, **kw) for i in range(n)]


# --- .fit d'activité ----------------------------------------------------------

def test_activity_fit_is_valid_with_records_and_summary():
    m = decode(encode_activity(ride()))
    assert m["file_id_mesgs"][0]["type"] == "activity"
    assert len(m["record_mesgs"]) == 120
    session = m["session_mesgs"][0]
    assert session["sport"] == "cycling" and session["sub_sport"] == "indoor_cycling"
    assert session["total_timer_time"] == 120
    assert session["avg_power"] == 200 and session["normalized_power"] == 200
    assert session["avg_heart_rate"] == 140 and session["avg_cadence"] == 90
    assert session["total_distance"] == pytest.approx(1200, abs=1)  # 36 km/h = 10 m/s pendant 120 s
    last = m["record_mesgs"][-1]
    assert last["power"] == 200 and last["distance"] == pytest.approx(1200, abs=1)
    assert m["activity_mesgs"][0]["num_sessions"] == 1


def test_pause_stops_timer_and_distance():
    points = ride(60) + ride(60, start=T0 + 600)  # 9 minutes de pause au milieu
    m = decode(encode_activity(points))
    session = m["session_mesgs"][0]
    assert session["total_timer_time"] == 120
    assert session["total_elapsed_time"] == 660
    assert session["total_distance"] == pytest.approx(1200, abs=1)
    events = [(e["event"], e["event_type"]) for e in m["event_mesgs"]]
    assert events == [("timer", "start"), ("timer", "stop"), ("timer", "start"), ("timer", "stop_all")]


def test_one_lap_per_brick_and_grade():
    points = ride(30, lap=0) + ride(30, start=T0 + 30, lap=1, grade_pct=-2.5)
    m = decode(encode_activity(points))
    assert len(m["lap_mesgs"]) == 2 and m["session_mesgs"][0]["num_laps"] == 2
    assert m["record_mesgs"][-1]["grade"] == -2.5
    assert "grade" not in m["record_mesgs"][0]


def test_missing_values_are_left_out():
    m = decode(encode_activity([ActivityPoint(T0 + i, 150) for i in range(10)]))
    record = m["record_mesgs"][0]
    assert record["power"] == 150 and "heart_rate" not in record and "cadence" not in record
    assert "avg_heart_rate" not in m["session_mesgs"][0]


def test_summary_and_file_name():
    s = summarize(ride(60))
    assert s.avg_speed_kmh == pytest.approx(36)
    assert s.work_j == pytest.approx(12_000)
    name = activity_file_name(T0, "Sweet spot : 3×10'")
    assert name.endswith("_Sweet-spot-3-10.fit") and name.startswith("2025-10-0")


# --- séances et mode libre -----------------------------------------------------

def test_workout_session_samples_become_points_with_laps():
    s = WorkoutSession(parse_workout("1m@100 1m@200"), 250)
    s.start()
    for i in range(120):
        s.record(150, 90, 130, 30.0, at=T0 + i)
        s.tick(1)
    points = ride_points(s.samples)
    assert len(points) == 120 and {p.lap for p in points} == {0, 1}
    assert points[0].speed_kmh == 30.0 and points[0].at == T0
    assert s.state is State.FINISHED
    title, text = ride_title(s)
    assert title == s.workout.name and "arrêtée" not in text


def test_free_ride_title_tells_slope():
    f = FreeRideSession(250)
    f.start()
    f.record(200, at=T0)
    f.mode = FreeMode.SLOPE
    f.record(200, at=T0 + 1)
    assert ride_title(f) == ("Mode libre", "Mode libre sur home trainer (ERG et pente simulée).")
    assert ride_points(f.samples)[1].grade_pct == 0.0


# --- réseau simulé ------------------------------------------------------------

class FakeHttp:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url, headers, body))
        status, data = self.responses.pop(0)
        return Response(status, json.dumps(data).encode())


def connected(book, key):
    a = book[key]
    a.client_id, a.client_secret = "123", "secret"
    a.access_token, a.refresh_token, a.expires_at = "tok", "ref", time.time() + 3600
    return a


@pytest.fixture
def book(tmp_path):
    return AccountBook(tmp_path / "comptes.json")


@pytest.fixture
def outbox(tmp_path):
    return Outbox(tmp_path / "sorties")


def test_strava_upload_waits_for_activity(book, tmp_path):
    connected(book, "strava")
    http = FakeHttp((201, {"id_str": "9", "status": "Your activity is still being processed."}),
                    (200, {"id_str": "9", "activity_id": 42}))
    service = Strava(book["strava"], http, sleep=lambda s: None)
    path = tmp_path / "s.fit"
    path.write_bytes(encode_activity(ride(60)))
    result = service.upload(path, "Sweet spot", "desc", "ext-1")
    assert result.url == "https://www.strava.com/activities/42"
    method, url, headers, body = http.calls[0]
    assert url == "https://www.strava.com/api/v3/uploads" and headers["Authorization"] == "Bearer tok"
    assert b'name="data_type"\r\n\r\nfit' in body and b'name="trainer"\r\n\r\n1' in body
    assert http.calls[1][1].endswith("/uploads/9")


def test_strava_duplicate_counts_as_sent(book, tmp_path):
    connected(book, "strava")
    http = FakeHttp((201, {"id": 9, "error": "s.fit duplicate of <a href='/activities/77'>Sortie</a>"}))
    path = tmp_path / "s.fit"
    path.write_bytes(b"x")
    result = Strava(book["strava"], http).upload(path, "t", "", "e")
    assert result.url.endswith("/77") and "déjà" in result.message


def test_strava_refreshes_expired_token(book):
    a = connected(book, "strava")
    a.expires_at = 0
    http = FakeHttp((200, {"access_token": "new", "refresh_token": "ref2", "expires_at": time.time() + 21600}))
    assert Strava(a, http).access_token() == "new"
    form = parse_qs(http.calls[0][3].decode())
    assert form["grant_type"] == ["refresh_token"] and form["refresh_token"] == ["ref"]
    assert form["client_secret"] == ["secret"]
    assert a.refresh_token == "ref2"


def test_nolio_upload_is_base64_json(book, tmp_path):
    connected(book, "nolio")
    http = FakeHttp((202, {}))
    path = tmp_path / "s.fit"
    path.write_bytes(b"FITDATA")
    result = Nolio(book["nolio"], http).upload(path, "Mode libre", "desc", "1759480000")
    assert result.message == "envoyée"
    method, url, headers, body = http.calls[0]
    assert url == "https://www.nolio.io/api/upload/file/" and headers["Content-Type"] == "application/json"
    payload = json.loads(body)
    assert payload["format"] == "fit" and base64.b64decode(payload["data"]) == b"FITDATA"
    assert payload["id_partner"] == "1759480000" and payload["title"] == "Mode libre"


def test_nolio_token_uses_basic_auth(book):
    a = book["nolio"]
    a.client_id, a.client_secret = "id", "sec"
    http = FakeHttp((200, {"access_token": "a", "refresh_token": "r", "expires_in": 86400}))
    Nolio(a, http).exchange_code("CODE", "http://localhost:8765/nolio")
    headers, body = http.calls[0][2], parse_qs(http.calls[0][3].decode())
    assert headers["Authorization"] == "Basic " + base64.b64encode(b"id:sec").decode()
    assert body == {"grant_type": ["authorization_code"], "code": ["CODE"],
                    "redirect_uri": ["http://localhost:8765/nolio"]}
    assert a.connected and a.expires_at > time.time() + 80000


def test_outbox_sends_pending_and_retries_failures(book, outbox):
    connected(book, "strava")
    connected(book, "nolio")
    path = outbox.add(ride(), "Sweet spot", "Séance")
    assert path.exists() and outbox.pending(["strava", "nolio"]) == [(path.name, "strava"), (path.name, "nolio")]
    http = FakeHttp((201, {"id": 1, "activity_id": 5}), (500, {"detail": "panne"}))
    reports = send_pending(outbox, book, http)
    assert [r.ok for r in reports] == [True, False]
    assert "HTTP 500" in reports[1].message
    assert outbox.pending(["strava", "nolio"]) == [(path.name, "nolio")]
    send_pending(outbox, book, FakeHttp((202, {})))
    assert outbox.pending(["strava", "nolio"]) == []
    assert AccountBook.load(book.path)["nolio"].connected  # comptes enregistrés


def test_outbox_skips_accounts_not_connected_or_manual(book, outbox):
    connected(book, "strava").auto = False
    outbox.add(ride(), "Mode libre")
    assert send_pending(outbox, book, FakeHttp()) == []


def test_not_connected_raises(book):
    with pytest.raises(SyncError, match="non connecté"):
        Strava(book["strava"]).access_token()


def test_accounts_round_trip(book):
    connected(book, "strava").athlete = "Le B"
    book.save()
    again = AccountBook.load(book.path)
    assert again["strava"].athlete == "Le B" and again["strava"].connected
    assert not again["nolio"].connected


def test_multipart_has_file():
    body, content_type = multipart({"a": "1"}, {"file": ("x.fit", b"\x00\x01")})
    boundary = content_type.split("boundary=")[1]
    assert body.startswith(f"--{boundary}".encode()) and b'filename="x.fit"' in body and b"\x00\x01" in body


# --- connexion OAuth (navigateur simulé) ---------------------------------------

def _browser(follow):
    """Faux navigateur : l'utilisateur accepte, le service redirige vers localhost avec le code."""
    def open_browser(url):
        query = parse_qs(urlparse(url).query)
        target = query["redirect_uri"][0] + "?" + follow(query)
        threading.Thread(target=lambda: urllib.request.urlopen(target, timeout=5).read(), daemon=True).start()
    return open_browser


def test_connect_catches_code_on_localhost(book):
    a = book["strava"]
    a.client_id, a.client_secret = "123", "secret"
    http = FakeHttp((200, {"access_token": "a", "refresh_token": "r", "expires_at": time.time() + 3600,
                           "athlete": {"firstname": "Le", "lastname": "B"}}))
    connect(Strava(a, http), book, _browser(lambda q: f"state={q['state'][0]}&code=XYZ&scope=activity:write"),
            port=18765, timeout_s=10)
    assert parse_qs(http.calls[0][3].decode())["code"] == ["XYZ"]
    assert a.athlete == "Le B" and AccountBook.load(book.path)["strava"].connected


def test_connect_refused_or_wrong_state(book):
    a = book["strava"]
    a.client_id, a.client_secret = "123", "secret"
    with pytest.raises(SyncError, match="access_denied"):
        connect(Strava(a, FakeHttp()), book, _browser(lambda q: f"state={q['state'][0]}&error=access_denied"),
                port=18766, timeout_s=10)
    with pytest.raises(SyncError, match="state"):
        wait_for_code("strava", "attendu", port=18767, timeout_s=10,
                      ready=lambda: _browser(lambda q: "state=autre&code=1")(
                          "http://x/?redirect_uri=http://localhost:18767/strava"))


def test_durations_follow_the_ride_timer():
    # Horloge peu précise (mesures groupées) : le chronomètre de la séance fait foi.
    points = [ActivityPoint(T0 + i // 2, 200, speed_kmh=36.0, timer_s=float(i)) for i in range(60)]
    assert summarize(points).distance_m == pytest.approx(600)
