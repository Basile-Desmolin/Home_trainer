"""Bruit seconde à seconde de deux .fit (puissance, cadence, FC).

Usage : python bruit.py APPLI.fit GARMIN.fit
"""
import sys

import numpy as np
from garmin_fit_sdk import Decoder, Stream


def recs(path):
    m, _ = Decoder(Stream.from_file(path)).read(convert_datetimes_to_dates=False)
    rs = m["record_mesgs"]
    while rs and not rs[-1].get("power"):  # secondes à 0 W après l'arrêt
        rs = rs[:-1]
    return rs


ra, rg = recs(sys.argv[1]), recs(sys.argv[2])
for champ in ("power", "cadence", "heart_rate"):
    a = np.array([r.get(champ) or 0 for r in ra], float)
    g = np.array([r.get(champ) or 0 for r in rg], float)
    da, dg = np.diff(a), np.diff(g)
    print(f"{champ:10s} variation d'une seconde à l'autre (écart-type) : Appli {da.std():.2f}, Garmin {dg.std():.2f}"
          f" (rapport {da.std() / dg.std():.2f}) ; |variation| > 10 : Appli {(np.abs(da) > 10).sum()},"
          f" Garmin {(np.abs(dg) > 10).sum()} ; valeur répétée : Appli {(da == 0).mean() * 100:.0f} %,"
          f" Garmin {(dg == 0).mean() * 100:.0f} %")

ts = np.array([r["timestamp"] for r in ra])
p = np.array([r.get("power") or 0 for r in ra], float)
d = np.diff(ts)
pres = np.zeros(len(d), bool)
for i in np.where(d != 1)[0]:
    pres[max(0, i - 3):i + 4] = True
dp = np.diff(p)
if pres.any():
    print(f"Puissance Appli, variation près des doublons/trous {dp[pres].std():.2f} W ({pres.sum()} s)"
          f" contre ailleurs {dp[~pres].std():.2f} W")

for nom, rs in (("Appli", ra), ("Garmin", rg)):
    x = np.array([r.get("power") or 0 for r in rs], float)  # point sans puissance (capteur perdu) : 0
    x = x - np.convolve(x, np.ones(30) / 30, "same")  # tendance lente retirée
    print(f"Autocorrélation à 1 s de la puissance (tendance retirée) : {nom} {np.corrcoef(x[:-1], x[1:])[0, 1]:.3f}")
