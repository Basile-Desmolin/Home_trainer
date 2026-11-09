"""Figure de comparaison application / Garmin.

Usage : python figure.py APPLI.fit SERIES.npz FIGURE.png   (SERIES.npz écrit par `compare.py`)
"""
import sys

import matplotlib
import numpy as np
from garmin_fit_sdk import Decoder, Stream

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

APP, NPZ, OUT = sys.argv[1], sys.argv[2], sys.argv[3]
z = np.load(NPZ)
A, G, Ac, Gc = z["A"], z["G"], z["Ac"], z["Gc"]
msgs, _ = Decoder(Stream.from_file(APP)).read(convert_datetimes_to_dates=False)
ts = np.array([r["timestamp"] for r in msgs["record_mesgs"]])
lag = ts - ts[0] - np.arange(len(ts))
d = np.diff(ts)


def lisse(x, n):
    k = np.ones(n)
    v = np.where(np.isnan(x), 0, x)
    c = np.convolve(~np.isnan(x), k, "same")
    return np.convolve(v, k, "same") / np.maximum(c, 1)


t = np.arange(len(A)) / 60
C_APP, C_GAR = "#2a6fdb", "#e0702b"
fig, ax = plt.subplots(4, 1, figsize=(12, 13), constrained_layout=True)

e10 = lisse(A, 10) - lisse(G, 10)
ax[0].plot(t, lisse(G, 10), color=C_GAR, lw=1.2, label="Garmin")
ax[0].plot(t, lisse(A, 10), color=C_APP, lw=1.0, label="Application")
ax[0].set_title(f"Puissance lissée sur 10 s (écart-type de l'écart {np.nanstd(e10):.1f} W)")
ax[0].set_xlabel("minutes")
ax[0].set_ylabel("W")
ax2 = ax[0].twinx()
ax2.plot(t, e10, color="#888", lw=0.6)
ax2.set_ylim(-20, 60)
ax2.set_ylabel("écart lissé (gris), W")
ax[0].legend(loc="lower right")

ig = int(np.nanargmax(G))
w0, w1 = max(0, ig - 40), min(len(G), ig + 40)
ax[1].step(np.arange(w0, w1), G[w0:w1], where="mid", color=C_GAR, label="Garmin")
ax[1].step(np.arange(w0, w1), A[w0:w1], where="mid", color=C_APP, label="Application")
ax[1].set_title(f"Zoom autour du maximum (Garmin {np.nanmax(G):.0f} W, application {np.nanmax(A[w0:w1]):.0f} W)")
ax[1].set_xlabel("s depuis le début")
ax[1].set_ylabel("W")
ax[1].legend()

tt = (ts - ts[0]) / 60
dup, gap = np.where(d == 0)[0], np.where(d >= 2)[0]
ax[2].plot(tt, lag, color=C_APP, lw=1.2)
ax[2].plot(tt[dup], lag[dup], "v", color="#c0392b", ms=4, label=f"seconde doublée ({len(dup)})")
ax[2].plot(tt[gap], lag[gap], "^", color="#27ae60", ms=4, label=f"seconde manquante ({len(gap)})")
ax[2].set_title(f"Retard du chrono de l'application sur l'horloge : {lag[-1]:.0f} s à la fin")
ax[2].set_xlabel("minutes")
ax[2].set_ylabel("horloge − n° de mesure (s)")
ax[2].legend(loc="upper left")

dc = Ac - Gc
dc = dc[~np.isnan(dc)]
ax[3].hist(dc, bins=np.arange(-3.5, 4.5, 1), color=C_APP, alpha=0.8)
ax[3].set_title(f"Écart de cadence application − Garmin, seconde par seconde (moyenne {dc.mean():+.2f} tr/min)")
ax[3].set_xlabel("écart (tr/min)")
ax[3].set_ylabel("secondes")
fig.savefig(OUT, dpi=90)
print("figure :", OUT)
