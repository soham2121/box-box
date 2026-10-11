"""Out-of-sample check of who pits under an SC/VSC in the sim, three ways:
  worn rule   a car stops when its tyres are past 40 % of their life with more than 8 laps left
  per car     fitted odds per car (tyre age, laps left, owes the two-compound rule, which lap of it, SC or VSC)
  + herd      the same plus a factor shared by every car in one SC/VSC (MODEL 'pit_neutral_sd'): what the app uses
Each era is fitted on its earlier seasons and tested on its latest one, which the fit never saw (2026: its first
14 races/sprints vs the rest). Everything else in the calibration is the same for all three.

Run: .venv\\Scripts\\python scripts\\validate_pit_neutral.py   (about half an hour; races come from the disk cache)
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding='utf-8')
import fastf1
import numpy as np
fastf1.set_log_level('ERROR')
from boxbox import calibrate as c, sim

SPLITS = [('2018-2021', [2018, 2019, 2020], [2021]), ('2022-2025', [2022, 2023, 2024], [2025]), ('2026-', [2026], None)]
T, H = np.polynomial.hermite.hermgauss(24)


def load(years):
    out = []
    for label, p in c.races(years):
        try:
            r = c.load(p)
        except Exception:
            continue
        if r['total'] and len(r['laps']) > 100:
            out.append({**r, 'label': label})
    return out


def worn(race, ep, life):
    """The worn rule's yes/no for each car-lap of one SC/VSC period, from its features."""
    return np.array([x[1] * sim.DEFAULTS['life']['MEDIUM'] > 0.4 * life['MEDIUM'] and x[2] * race['total'] > 8
                     for x in ep['x']], float)  # ponytail: features keep no compound, so MEDIUM's life stands in


def episode_stats(ep, w, sd):
    """Per car-lap marginal P(pit), and the expected number of stops in the period with its variance."""
    z = np.array(ep['x']) @ w
    u = np.sqrt(2) * sd * T if sd else np.zeros(1)
    k = H / np.sqrt(np.pi) if sd else np.ones(1)
    p = 1 / (1 + np.exp(-(z[:, None] + u[None, :])))  # car-lap x quadrature node
    mean = p @ k
    total = p.sum(0)
    return mean, float(total @ k), float((p * (1 - p)).sum(0) @ k + (total ** 2) @ k - (total @ k) ** 2)


def pit_report(test, cal, w_ind):
    eps = [(r, e) for r in test for e in c.neutral_pits(r)]
    y = np.concatenate([np.array(e['y'], float) for _, e in eps])
    kinds = np.concatenate([[e['kind']] * len(e['y']) for _, e in eps])
    rows = {'worn rule': ([worn(r, e, cal['life']) for r, e in eps], None),
            'per car': [episode_stats(e, w_ind, 0) for _, e in eps],
            '+ herd': [episode_stats(e, cal['pit_neutral'], cal['pit_neutral_sd'][e['kind']]) for _, e in eps]}
    obs = np.array([sum(e['y']) for _, e in eps])
    print(f'    pit decisions: {len(y)} car-laps in {len(eps)} unseen SC/VSC periods, {int(y.sum())} stops')
    for name, v in rows.items():
        if name == 'worn rule':
            p = np.concatenate(v[0])
            exp = np.array([x.sum() for x in v[0]])
            disp = float(np.sum((obs - exp) ** 2) / max(np.sum([(x * (1 - x)).sum() for x in v[0]]), 1e-9))
        else:
            p = np.concatenate([m for m, _, _ in v])
            exp, var = np.array([e for _, e, _ in v]), np.array([s for _, _, s in v])
            disp = float(np.sum((obs - exp) ** 2) / np.sum(var))
        q = np.clip(p, 0.02, 0.98)  # the rule says 0 or 1: clipped, else its log loss is infinite on any miss
        print(f'    {name:<9} Brier {np.mean((p - y) ** 2):.4f}  log loss {-np.mean(y * np.log(q) + (1 - y) * np.log(1 - q)):.3f}'
              f'  stops per period off by {np.mean(np.abs(obs - exp)):.1f} (spread ratio {disp:.1f}, 1 = right)'
              + ''.join(f"  {k.upper()} {p[kinds == k].mean():.1%} vs real {y[kinds == k].mean():.1%}"
                        for k in ('sc', 'vsc') if (kinds == k).any()))
    p = np.concatenate([m for m, _, _ in rows['+ herd']])
    print('    + herd reliability (predicted -> real, n):', '  '.join(
        f'{lo:.0%}-{hi:.0%}: {p[m].mean():.0%}->{y[m].mean():.0%} ({m.sum()})'
        for lo, hi in ((0, .1), (.1, .25), (.25, .5), (.5, .75), (.75, 1.01)) for m in [(p >= lo) & (p < hi)] if m.sum()))


for era, train_years, test_years in SPLITS:
    if test_years:
        train, test = load(train_years), load(test_years)
    else:  # one season so far: its first 14 races/sprints vs the rest
        rs = load(train_years)
        train, test = rs[:14], rs[14:]
    sums = [c.summary(r) for r in train]
    cal = c.calibrate(sums)
    w_ind = c.fit_logistic([x for s in sums for e in s['neutral_pits'] for x in e['x']],
                           [y for s in sums for e in s['neutral_pits'] for y in e['y']])
    print(f'\n== {era}: fitted on {len(train)} ({train[0]["label"]} .. {train[-1]["label"]}), '
          f'tested on {len(test)} unseen ({test[0]["label"]} .. {test[-1]["label"]})')
    print(f"    shared SC/VSC factor: sd SC {cal['pit_neutral_sd']['sc']}, VSC {cal['pit_neutral_sd']['vsc']}")
    variants = {'worn rule': {'pit_neutral': None}, 'per car': {'pit_neutral': w_ind, 'pit_neutral_sd': None},
                '+ herd': {}}
    for name, over in variants.items():
        v = [c.score(r, {**r['m0'], **cal, **over}) for r in test]
        print(f'    {name:<9} ' + '  '.join(f"{k} {np.nanmean([x[k] for x in v]):.3f}"
                                      for k in ('pos_err', 'rho', 'logp', 'rps', 'cover80')))
    pit_report(test, cal, w_ind)
