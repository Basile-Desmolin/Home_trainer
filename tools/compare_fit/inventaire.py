"""Inventaire des messages d'un ou plusieurs fichiers .fit (sans tout afficher).

Usage : python inventaire.py SORTIE.fit [AUTRE.fit ...]
"""
import sys
from collections import Counter

from garmin_fit_sdk import Decoder, Stream

COURTS = ("file_id_mesgs", "device_info_mesgs", "session_mesgs", "activity_mesgs",
          "sport_mesgs", "user_profile_mesgs", "zones_target_mesgs", "workout_mesgs",
          "developer_data_id_mesgs", "field_description_mesgs", "device_settings_mesgs")
TOUR = ("start_time", "timestamp", "total_elapsed_time", "total_timer_time", "total_distance", "avg_power",
        "max_power", "normalized_power", "avg_cadence", "avg_heart_rate", "lap_trigger", "left_right_balance",
        "avg_left_torque_effectiveness", "avg_left_pedal_smoothness")


def charge(path):
    return Decoder(Stream.from_file(path)).read(convert_datetimes_to_dates=True, merge_heart_rates=False)


for path in sys.argv[1:]:
    print("=" * 80)
    print(path)
    msgs, errs = charge(path)
    print("erreurs :", errs)
    for k, v in msgs.items():
        print(f"  {k}: {len(v)}")
    for k in COURTS:
        for m in msgs.get(k, [])[:12]:
            print(f"--- {k}")
            print("   ", {a: b for a, b in m.items() if b is not None})
    for i, m in enumerate(msgs.get("lap_mesgs", [])):
        print(f"--- tour {i}", {a: m.get(a) for a in TOUR})
    ev = msgs.get("event_mesgs", [])
    print("--- événements :", len(ev))
    for m in ev[:40]:
        print("   ", {a: m.get(a) for a in ("timestamp", "event", "event_type", "data", "timer_trigger")})
    recs = msgs.get("record_mesgs", [])
    champs = Counter(a for r in recs for a, b in r.items() if b is not None)
    print("--- champs des points :", dict(champs))
    for r in recs[:3] + recs[-2:]:
        print("   ", r)
