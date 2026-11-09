"""Comparaison point à point de deux .fit d'une même sortie (application / Garmin).

Usage : python compare.py APPLI.fit GARMIN.fit SERIES.npz
Le décalage d'horloge est celui qui rend le plus de puissances égales (même capteur des deux côtés) ;
SERIES.npz reçoit les séries alignées pour `derive.py` et `figure.py`.
"""
import sys
from collections import Counter

import numpy as np
from garmin_fit_sdk import Decoder, Stream

APP, GAR, NPZ = sys.argv[1], sys.argv[2], sys.argv[3]


def recs(path):
    msgs, errs = Decoder(Stream.from_file(path)).read(convert_datetimes_to_dates=False, merge_heart_rates=False)
    assert not errs, errs
    return msgs["record_mesgs"]


def serie(rs, champ):
    return {r["timestamp"]: r.get(champ) for r in rs}


def np_power(p):
    p = np.asarray(p, float)
    if len(p) < 30:
        return float("nan")
    k = np.convolve(p, np.ones(30) / 30, mode="valid")
    return float(np.mean(k ** 4) ** 0.25)


def mmp(p, d):
    p = np.asarray(p, float)
    return float(np.max(np.convolve(p, np.ones(d) / d, mode="valid")))


ra, rg = recs(APP), recs(GAR)
for nom, rs in (("Appli", ra), ("Garmin", rg)):
    ts = [r["timestamp"] for r in rs]
    d = np.diff(ts)
    print(f"{nom}: {len(rs)} points, de {ts[0]} à {ts[-1]} (étendue {ts[-1]-ts[0]} s)")
    print(f"   écarts entre points : {dict(sorted(Counter(d.tolist()).items()))}")
    trous = [(i, ts[i], int(d[i])) for i in range(len(d)) if d[i] != 1]
    print(f"   écarts ≠ 1 s (indice, horodatage, écart) : {trous[:40]}")

pa, pg = serie(ra, "power"), serie(rg, "power")
best = sorted(((sum(1 for t, v in pa.items() if (t + off) in pg and pg[t + off] == v), off)
               for off in range(-15, 16)), reverse=True)
print("\nDécalage (Garmin = Appli + off) → points de puissance égaux :", best[:5])
OFF = best[0][1]

t0 = max(min(pa) + OFF, min(pg))
t1 = min(max(pa) + OFF, max(pg))
axe = list(range(t0, t1 + 1))
print(f"Plage commune (temps Garmin) : {t0} → {t1}, {len(axe)} s")


def aligne(champ):
    a, g = serie(ra, champ), serie(rg, champ)
    A = np.array([np.nan if a.get(t - OFF) is None else a[t - OFF] for t in axe], float)
    G = np.array([np.nan if g.get(t) is None else g[t] for t in axe], float)
    return A, G


A, G = aligne("power")
print(f"Secondes absentes dans la plage commune : Appli {int(np.isnan(A).sum())}, Garmin {int(np.isnan(G).sum())}")
ok = ~np.isnan(A) & ~np.isnan(G)
diff = A[ok] - G[ok]
print(f"Puissance : égale sur {int((diff == 0).sum())}/{int(ok.sum())} s ; |écart| ≤ 2 W : "
      f"{int((np.abs(diff) <= 2).sum())} ; moyenne écart {diff.mean():+.2f} W ; écart-type {diff.std():.2f} W ;"
      f" max |écart| {np.abs(diff).max():.0f} W")
print("   répartition des écarts (Appli - Garmin) :",
      dict(sorted(Counter(np.clip(diff, -50, 50).astype(int).tolist()).items())))
nz = [i for i in np.where(ok)[0] if A[i] != G[i]]
voisin = sum(1 for i in nz if any(0 <= i + k < len(G) and G[i + k] == A[i] for k in (-2, -1, 1, 2)))
print(f"   points différents : {len(nz)}, dont {voisin} égaux à la valeur Garmin d'une seconde voisine (±2 s)")

for nom, rs in (("Appli", ra), ("Garmin", rg)):
    p = np.array([r.get("power") or 0 for r in rs], float)  # point sans puissance (capteur perdu) : 0
    print(f"{nom} sur son fichier : moy {p.mean():.1f} W, NP {np_power(p):.1f} W, max {p.max():.0f} W,"
          f" MMP 5s {mmp(p, 5):.0f}, 1min {mmp(p, 60):.0f}, 5min {mmp(p, 300):.0f}, 20min {mmp(p, 1200):.0f},"
          f" zéros {int((p == 0).sum())}")
print(f"Plage commune : Appli moy {np.nanmean(A):.2f} / Garmin {np.nanmean(G):.2f} W")
ig = int(np.nanargmax(G))
print(f"Max Garmin {G[ig]:.0f} W à s={ig} ; Appli autour : {A[max(0, ig-4):ig+5]} ; Garmin : {G[max(0, ig-4):ig+5]}")

for champ in ("cadence", "heart_rate"):
    A2, G2 = aligne(champ)
    ok2 = ~np.isnan(A2) & ~np.isnan(G2)
    d2 = A2[ok2] - G2[ok2]
    print(f"\n{champ} : égale {int((d2 == 0).sum())}/{int(ok2.sum())}, moy écart {d2.mean():+.2f},"
          f" max |écart| {np.abs(d2).max():.0f}, zéros Appli {int((A2 == 0).sum())}, Garmin {int((G2 == 0).sum())}")
    print("   répartition :", dict(sorted(Counter(np.clip(d2, -20, 20).astype(int).tolist()).items())))

Ac, Gc = aligne("cadence")
print("\nCadence > 0 avec puissance 0 : Appli", int(((Ac > 0) & (A == 0)).sum()),
      " Garmin", int(((Gc > 0) & (G == 0)).sum()))
print("Cadence = 0 avec puissance > 0 : Appli", int(((Ac == 0) & (A > 0)).sum()),
      " Garmin", int(((Gc == 0) & (G > 0)).sum()))

sp = np.array([r.get("speed") or 0 for r in ra], float) * 3.6
cad = np.array([r.get("cadence") or 0 for r in ra], float)
m = cad > 0
if sp.any():
    print(f"\nVitesse Appli : moy {sp.mean():.2f} km/h, max {sp.max():.2f} ; vitesse/cadence "
          f"{np.mean(sp[m] / cad[m]):.4f} ± {np.std(sp[m] / cad[m]):.4f} ; corr cad {np.corrcoef(sp, cad)[0, 1]:.3f},"
          f" corr P {np.corrcoef(sp, [r.get('power') or 0 for r in ra])[0, 1]:.3f}")

Ah, Gh = aligne("heart_rate")
np.savez(NPZ, A=A, G=G, Ac=Ac, Gc=Gc, Ah=Ah, Gh=Gh, speed=sp, off=OFF)
