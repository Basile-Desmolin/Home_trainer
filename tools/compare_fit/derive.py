"""Dérive du chrono d'enregistrement de l'application, décalage local et écart après lissage.

Usage : python derive.py APPLI.fit GARMIN.fit SERIES.npz   (SERIES.npz écrit par `compare.py`)
"""
import sys

import numpy as np
from garmin_fit_sdk import Decoder, Stream

APP, NPZ = sys.argv[1], sys.argv[3]
msgs, _ = Decoder(Stream.from_file(APP)).read(convert_datetimes_to_dates=False)
ts = np.array([r["timestamp"] for r in msgs["record_mesgs"]])
d = np.diff(ts)
print("Instants (s depuis le début) des écarts ≠ 1 s, par paquets :")
paquets = []
for i in np.where(d != 1)[0]:
    t = int(ts[i] - ts[0])
    if paquets and t - paquets[-1][1] <= 30:
        paquets[-1][1] = t
        paquets[-1][2] += 1
    else:
        paquets.append([t, t, 1])
for p in paquets:
    print(f"   {p[0]:5d} → {p[1]:5d} s : {p[2]} anomalies")
centres = [(p[0] + p[1]) / 2 for p in paquets]
print("   intervalle entre paquets :", np.round(np.diff(centres)).tolist())
periode = (ts[-1] - ts[0]) / (len(ts) - 1)
if periode > 1:
    print(f"Points : {len(ts)} ; période moyenne d'enregistrement {periode:.5f} s ;"
          f" une seconde de dérive tous les {1 / (periode - 1):.0f} s")

z = np.load(NPZ)
A, G = z["A"], z["G"]
print("\nDécalage local (fenêtres de 300 s) : meilleur k (Appli[i] = Garmin[i+k]) et taux d'égalité")
for w0 in range(0, len(A) - 300, 300):
    res = []
    for k in range(-3, 4):
        if w0 + k < 0 or w0 + 300 + k > len(G):
            continue
        a, g = A[w0:w0 + 300], G[w0 + k:w0 + 300 + k]
        ok = ~np.isnan(a) & ~np.isnan(g)
        res.append(((a[ok] == g[ok]).mean(), k))
    res.sort(reverse=True)
    print(f"   {w0:5d} s : k={res[0][1]:+d} ({res[0][0]*100:.0f} %), puis k={res[1][1]:+d} ({res[1][0]*100:.0f} %)")


def lisse(x, n):
    v = np.where(np.isnan(x), 0, x)
    c = np.convolve(~np.isnan(x), np.ones(n), "full")[:len(x)]
    return np.convolve(v, np.ones(n), "full")[:len(x)] / np.maximum(c, 1)


print("\nÉcart après lissage (Appli - Garmin) :")
for n in (1, 3, 10, 30, 60):
    e = lisse(A, n) - lisse(G, n)
    print(f"   {n:3d} s : écart-type {np.nanstd(e):.2f} W, |écart| 95e centile {np.nanpercentile(np.abs(e), 95):.1f} W,"
          f" max {np.nanmax(np.abs(e)):.1f} W")
