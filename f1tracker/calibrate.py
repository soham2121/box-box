"""Strategy-sim calibration over archived races, one fit per regulation era (sim.ERAS), with a leave-one-race-out
backtest. Each era is a different car, so no era borrows another's numbers.

Run: .venv\\Scripts\\python -m f1tracker.calibrate [years...] [--write]
  years pick the eras to fit (default: every era, all its seasons). --write stores them in f1tracker/calibration.json
  under "eras"; sim.season_model() applies the session's era (config.json "model" overrides still win).

Fitted per era from its races:
  sc_per_race, sc_laps, vsc_per_race, vsc_laps   TrackStatus episodes during the race (4 = SC, 6/7 = VSC)
  sc_lap_factor, vsc_lap_factor                  lap time under SC / VSC vs the race's green median
  pass                                           logistic P(car within 1 s gets past within a lap | pace edge, gap)
  pass_restart                                   the same, on the laps after an SC / red-flag restart (no DRS / OM yet)
  red_per_race                                   red flags per race
  pit_neutral, pit_neutral_sd                    P(a car pits on an SC / VSC lap | tyre age, laps left, owes the
                                                 two-compound rule, which lap of it, VSC or SC), plus a shared
                                                 per-SC/VSC factor: teams mostly all pit or all stay out
  prior                                          dry compound offset vs MEDIUM and wear per lap (median race fit)
  life                                           tyre age the longest stints reach (90th percentile at the in-lap)
Every number in the backtest table is out of sample: the race being scored is left out of the fit.
"""
import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np
from scipy.optimize import minimize

from . import data, live, sim

OUT = Path(__file__).with_name('calibration.json')
STARTS = (0.3, 0.5, 0.7)  # backtest the sim from these shares of race distance


def races(years, sprints=True):
    """[(label, path)] of every finished race (and sprint) in those seasons."""
    kinds = ('Race', 'Sprint') if sprints else ('Race',)
    return [(f'{y} {m}' + ('' if s == 'Race' else ' sprint'), p)
            for y in years for m, ss in data.replay_meetings(y) for s, p in ss if s in kinds]


def episodes(path):
    """[(kind, first leader lap, laps)] for SC / VSC / red flag periods between the start and the flag."""
    status = data.archive_stream(path, 'SessionStatus')
    t0 = next((t for t, x in status if x.get('Status') == 'Started'), None)
    t1 = max((t for t, x in status if x.get('Status') == 'Finished'), default=float('inf'))
    if t0 is None:
        return []
    laps = [(t, int(x['CurrentLap'])) for t, x in data.archive_stream(path, 'LapCount') if x.get('CurrentLap')]
    lap_at = lambda t: max((n for s, n in laps if s <= t), default=1)
    kind = {'4': 'sc', '5': 'red', '6': 'vsc', '7': 'vsc'}
    out, cur = [], None  # cur = [kind, first lap]
    for t, x in data.archive_stream(path, 'TrackStatus'):
        k = kind.get(x.get('Status'))
        if t < t0 or t > t1 or (cur and k == cur[0]):
            continue
        if cur:
            out.append((cur[0], cur[1], lap_at(t) - cur[1] + 1))
        cur = [k, lap_at(t)] if k else None
    return out


def load(path):
    """Everything the calibration needs from one race. m0: its season's knobs without any calibration."""
    st, laps = live.laps_from_archive(path)
    laps = sim.relabel(laps, sim.relative_compounds(laps))  # 2018's seven compound names -> SOFT/MEDIUM/HARD
    total = st.session()['total'] or max((l['lap'] for l in laps), default=0)
    year = int(path[:4])
    return {'path': path, 'year': year, 'laps': laps, 'total': total, 'episodes': episodes(path),
            'pit_loss': st.pit_loss(), 'm0': sim.season_model(year, calibrated=False)}


# ---- measurements ------------------------------------------------------------------------------------------------
def lap_factors(race):
    """{'sc': [...], 'vsc': [...]}: each neutralised lap's field median over the race's green median."""
    by_lap = {}
    for l in race['laps']:
        if l['t'] and l['lap'] > 1 and not l['pit']:
            by_lap.setdefault(l['lap'], []).append(l['t'])
    med = {n: float(np.median(v)) for n, v in by_lap.items() if len(v) >= 5}
    neutral = {n for k, a, n_laps in race['episodes'] for n in range(a, a + n_laps)}
    green = [v for n, v in med.items() if n not in neutral]
    out = {'sc': [], 'vsc': []}
    if not green:
        return out
    g = np.median(green)
    for k, a, n_laps in race['episodes']:
        if k in out:  # whole laps inside the period only: the first and last are part green
            out[k] += [med[n] / g for n in range(a + 1, a + n_laps - 1) if n in med]
    return out


def restart_laps(race):
    """Laps right after an SC / red-flag restart, before DRS / Overtake Mode comes back (restart_laps per season)."""
    k = race['m0']['restart_laps']
    return {e + j for kind, a, n in race['episodes'] if kind in ('sc', 'red') for e in [a + n - 1] for j in range(1, k + 1)}


def pass_events(race, gap_max=1.0, restart=False):
    """[(pace edge s, gap s, passed)] for every pair running within gap_max at the end of a green lap (restart=True:
    at the end of the lap before a restart lap, i.e. the bunched field). Pace edge = how much faster the car behind
    should be on the next lap (race-fitted model, same as the sim's)."""
    laps, total = race['laps'], race['total']
    model = sim.fit(laps, total, race['m0'])
    by = {(l['drv'], l['lap']): l for l in laps}
    neutral = {n for _, a, k in race['episodes'] for n in range(a - 1, a + k + 1)} | {l['lap'] for l in laps if l['sc']}
    after = restart_laps(race)
    out = []
    for n in range(2, total):
        if restart:
            if n + 1 not in after:
                continue
        elif n in neutral or n + 1 in neutral or n + 1 in after:
            continue
        rows = sorted((l for l in laps if l['lap'] == n and l['pos']), key=lambda l: l['pos'])
        for a, b in zip(rows, rows[1:]):
            na, nb = by.get((a['drv'], n + 1)), by.get((b['drv'], n + 1))
            if not (na and nb and b['int'] is not None and 0 < b['int'] <= gap_max):
                continue
            if a['pit'] or b['pit'] or na['pit'] or nb['pit'] or by.get((a['drv'], n + 2), {}).get('pit') or by.get((b['drv'], n + 2), {}).get('pit'):
                continue  # in-laps / out-laps: positions change in the pit lane, not on track
            edge = sim.predict(model, a['drv'], n + 1, na['cmp'], na['age']) - sim.predict(model, b['drv'], n + 1, nb['cmp'], nb['age'])
            out.append((edge, b['int'], nb['pos'] < na['pos']))
    return out


def fit_logistic(x, y):
    """Maximum-likelihood logistic regression weights (x already has the constant column)."""
    x, y = np.array(x, float), np.array(y, float)

    def nll(w):
        z = x @ w
        return float(np.sum(np.logaddexp(0, z) - y * z))
    return [round(float(v), 4) for v in minimize(nll, np.zeros(x.shape[1]), method='BFGS').x]


def fit_pass(events):
    """P(pass) = sigmoid(a + b * edge + c * gap)."""
    return fit_logistic([(1, e, g) for e, g, _ in events], [p for *_, p in events])


def neutral_pits(race):
    """One {'kind': 'sc' / 'vsc', 'x': [features], 'y': [pitted]} per SC / VSC period: every car on every lap of it,
    until it has pitted in that period (see sim.pit_neutral_features). Dry compounds only: wet stops follow the
    weather, not the SC."""
    laps, total = race['laps'], race['total']
    stops = {(s['drv'], s['lap']) for s in sim.stops_from_laps(laps)}
    gp, life = 'Sprint' not in race['path'], sim.DEFAULTS['life']
    used, out = {}, []  # (car, lap) -> compounds run up to that lap
    for l in sorted(laps, key=lambda l: (l['drv'], l['lap'])):
        prev = used.get((l['drv'], l['lap'] - 1), set())
        used[(l['drv'], l['lap'])] = prev | {l['cmp']}
    by_lap = {}
    for l in laps:
        by_lap.setdefault(l['lap'], []).append(l)
    for kind, a, n in race['episodes']:
        if kind not in ('sc', 'vsc'):
            continue
        done, ep = set(), {'kind': kind, 'x': [], 'y': []}
        for i, L in enumerate(range(a, a + n)):
            for l in by_lap.get(L, []):
                if l['drv'] in done or l['cmp'] not in DRY or not l['age']:
                    continue
                u = used.get((l['drv'], L), set())
                owes = gp and len(u & set(DRY)) < 2 and not u & {'INTERMEDIATE', 'WET'}
                pitted = (l['drv'], L) in stops
                ep['x'].append(sim.pit_neutral_features(l['age'] / life[l['cmp']], (total - L) / max(total, 1), owes, i,
                                                        kind == 'vsc'))
                ep['y'].append(pitted)
                if pitted:
                    done.add(l['drv'])
        if ep['y']:
            out.append(ep)
    return out


def fit_pit_neutral(episodes):
    """(weights, {'sc': sd, 'vsc': sd}): logistic odds per car plus a random intercept shared by every car in one SC /
    VSC period (normal, its own spread for SC and VSC), by maximum marginal likelihood (Gauss-Hermite). Real SCs are
    clustered: in 2021 and 2025 the stops per SC/VSC varied 9-18 times more than independent cars would give."""
    t, h = np.polynomial.hermite.hermgauss(24)
    logw = np.log(h / np.sqrt(np.pi))
    eps = [(np.array(e['x'], float), np.array(e['y'], float), e['kind'] == 'vsc') for e in episodes]
    w0 = fit_logistic([x for e in episodes for x in e['x']], [y for e in episodes for y in e['y']])

    def nll(params):
        w, sd = params[:-2], np.exp(params[-2:])
        out = 0.0
        for x, y, vsc in eps:
            z = (x @ w)[:, None] + np.sqrt(2) * sd[int(vsc)] * t[None, :]
            ll = (y[:, None] * z - np.logaddexp(0, z)).sum(0)
            out -= np.logaddexp.reduce(ll + logw)
        return out
    p = minimize(nll, np.r_[w0, 0.0, 0.0], method='BFGS').x
    return [round(float(v), 4) for v in p[:-2]], {'sc': round(float(np.exp(p[-2])), 3), 'vsc': round(float(np.exp(p[-1])), 3)}


DRY = ('SOFT', 'MEDIUM', 'HARD')


def tyre_samples(race):
    """{compound: (offset vs MEDIUM s, wear s/lap)} from a loosely anchored fit of the whole race; dry races only,
    compounds with >= 30 clean laps, and MEDIUM run too (offsets are relative to it)."""
    laps, m = race['laps'], race['m0']
    if sum(l['cmp'] in ('INTERMEDIATE', 'WET') for l in laps) > 0.05 * len(laps):
        return {}
    n = Counter(l['cmp'] for l in sim.clean(laps, m))
    if n['MEDIUM'] < 30:
        return {}
    f = sim.fit(laps, race['total'], {**m, 'prior_weight': 0.1})
    return {c: (f['off'][c] - f['off']['MEDIUM'], f['deg'][c]) for c in DRY if n[c] >= 30}


def stint_ends(race):
    """{compound: [tyre age at each in-lap]} (age counts laps the set ran earlier in the weekend, as the sim's does)."""
    by, out = {(l['drv'], l['lap']): l for l in race['laps']}, {}
    for s in sim.stops_from_laps(race['laps']):
        l = by.get((s['drv'], s['lap']))
        if l and l['cmp'] in DRY and l['age']:
            out.setdefault(l['cmp'], []).append(l['age'])
    return out


def summary(race):
    """One race's measurements, computed once: leave-one-out then only re-aggregates."""
    return {'episodes': race['episodes'], 'factors': lap_factors(race), 'events': pass_events(race),
            'restarts': pass_events(race, restart=True), 'neutral_pits': neutral_pits(race),
            'tyres': tyre_samples(race), 'stints': stint_ends(race)}


def calibrate(sums):
    """Knobs fitted on these races' summaries (one era)."""
    n, d = len(sums), sim.DEFAULTS
    ep = [e for s in sums for e in s['episodes']]
    fac = {k: [v for s in sums for v in s['factors'][k]] for k in ('sc', 'vsc')}
    count = lambda k: sum(1 for e in ep if e[0] == k)
    length = lambda k: float(np.median([e[2] for e in ep if e[0] == k])) if count(k) else d[k + '_laps']
    prior, life = dict(d['prior']), dict(d['life'])
    for c in DRY:
        x = [s['tyres'][c] for s in sums if c in s['tyres']]
        if len(x) >= 5:
            prior[c] = (round(float(np.median([o for o, _ in x])), 3), round(float(np.median([w for _, w in x])), 4))
        ages = [a for s in sums for a in s['stints'].get(c, [])]
        if len(ages) >= 20:
            life[c] = int(round(np.percentile(ages, 90)))
    return {'sc_per_race': round(count('sc') / n, 3), 'sc_laps': length('sc'),
            'vsc_per_race': round(count('vsc') / n, 3), 'vsc_laps': length('vsc'),
            'sc_lap_factor': round(float(np.median(fac['sc'])), 3) if fac['sc'] else d['sc_lap_factor'],
            'vsc_lap_factor': round(float(np.median(fac['vsc'])), 3) if fac['vsc'] else d['vsc_lap_factor'],
            'red_per_race': round(count('red') / n, 3),
            'pass': fit_pass([e for s in sums for e in s['events']]), 'prior': prior, 'life': life,
            'pass_restart': fit_pass(rs) if len(rs := [e for s in sums for e in s['restarts']]) >= 100 else None,
            **pit_neutral_fit([e for s in sums for e in s['neutral_pits']])}


def pit_neutral_fit(eps):
    """{'pit_neutral', 'pit_neutral_sd'} from these SC/VSC periods, or None for both with under 200 car-laps."""
    if sum(len(e['y']) for e in eps) < 200:
        return {'pit_neutral': None, 'pit_neutral_sd': None}
    return dict(zip(('pit_neutral', 'pit_neutral_sd'), fit_pit_neutral(eps)))


# ---- backtest -----------------------------------------------------------------------------------------------------
def score(race, m, runs=300, oracle=False):
    """Backtest one race with model knobs m: lap-time MAE, and from each STARTS point the finishing order
    (mean position error, rank correlation, log score of the real position under the simulated distribution)."""
    laps, total = race['laps'], race['total']
    m = {**m, 'two_compounds': 'Sprint' not in race['path']}  # sprints have free tyre choice
    last = {}
    for l in laps:
        last[l['drv']] = l if l['lap'] >= last.get(l['drv'], {'lap': -1})['lap'] else last[l['drv']]
    finished = {d for d, l in last.items() if l['lap'] >= total - 5}  # DNFs aren't modelled: score finishers only
    real = {d: r for r, d in enumerate(sorted(finished, key=lambda d: last[d]['pos']), 1)}
    out = {'mae': [], 'pos_err': [], 'rho': [], 'logp': [], 'rps': [], 'cover80': []}
    for frac in STARTS:
        at = max(3, int(total * frac))
        model = sim.fit([l for l in laps if l['lap'] <= at], total, m)
        ok, stint, pitted = {id(l) for l in sim.clean(laps, m)}, {l['drv']: l['cmp'] for l in laps if l['lap'] == at}, set()
        for l in sorted(laps, key=lambda l: l['lap']):
            if l['lap'] > at and l['pit']:
                pitted.add(l['drv'])
            if l['lap'] > at and l['drv'] not in pitted and id(l) in ok and stint.get(l['drv']) == l['cmp']:
                out['mae'].append(abs(sim.predict(model, l['drv'], l['lap'], l['cmp'], l['age']) - l['t']))
        snap = [d for d in sim.snapshot(laps, at) if d['drv'] in finished]
        if len(snap) < 5:
            continue
        now = next(((k.upper(), a + n - 1 - at) for k, a, n in race['episodes'] if a <= at + 1 <= a + n - 1), None)
        r = sim.simulate(snap, at, model, runs=runs, pit_loss=race['pit_loss'], neutral_now=now,
                         lap_s=sim.lap_time([l for l in laps if l['lap'] <= at]), conditions=track(race) if oracle else None)
        pos = np.argsort(np.argsort(r['pos'], axis=1), axis=1) + 1  # re-rank among the finishers
        truth = np.array([real[d] for d in r['drv']])
        mean = pos.mean(0)
        out['pos_err'].append(float(np.mean(np.abs(mean - truth))))
        out['rho'].append(float(np.corrcoef(np.argsort(np.argsort(mean)), truth - 1)[0, 1]))
        p = (pos == truth).mean(0)
        out['logp'].append(float(np.mean(np.log((p * runs + 0.5) / (runs + 0.5 * len(truth))))))
        # ranked probability score: squared gap between the simulated and the real cumulative position distribution,
        # 0 = certain and right (it rewards being close as well as being sure, unlike the log score)
        places = np.arange(1, len(truth) + 1)
        cdf = (pos[:, :, None] <= places).mean(0)
        out['rps'].append(float(np.mean(((cdf - (truth[:, None] <= places)) ** 2).sum(1) / (len(truth) - 1))))
        lo, hi = np.percentile(pos, 10, axis=0), np.percentile(pos, 90, axis=0)
        out['cover80'].append(float(np.mean((truth >= lo) & (truth <= hi))))  # ~0.8 when the spread is right
    out['rejoin'] = rejoin_errors(race)
    return {k: float(np.mean(v)) if v else float('nan') for k, v in out.items()}


def track(race):
    """{lap: 'DRY' / 'WET'} as it really was (what most cars ran on): oracle weather, to test the wet-tyre logic."""
    by = {}
    for l in race['laps']:
        by.setdefault(l['lap'], []).append(l['cmp'] in ('INTERMEDIATE', 'WET'))
    return {n: 'WET' if sum(v) * 2 > len(v) else 'DRY' for n, v in by.items()}


def rejoin_errors(race, lap_s=True):
    """'Pit now' rejoin position vs where each green-flag stop really came out (after the out-lap), in places.
    lap_s=False uses the old fixed 95 s lap for lapped cars."""
    laps, loss = race['laps'], race['pit_loss']['normal']
    by, out = {(l['drv'], l['lap']): l for l in laps}, []
    for l in laps:
        prev, nxt = by.get((l['drv'], l['lap'] - 1)), by.get((l['drv'], l['lap'] + 1))
        if l['pit'] and l['lap'] > 2 and prev and nxt and not (l['sc'] or prev['sc'] or nxt['sc']) and not prev['pit']:
            before = [o for o in laps if o['lap'] < l['lap']]
            snap = sim.snapshot(laps, l['lap'] - 1)
            if any(d['drv'] == l['drv'] for d in snap):
                pred = sim.rejoin(snap, l['drv'], loss, sim.lap_time(before) if lap_s else None)[0]
                out.append(abs(pred - nxt['pos']))
    return out


def fit_era(name, years):
    """Load the era's races, fit it, backtest leave-one-out. Returns (knobs, labels, rows) or None."""
    rs = []
    for label, path in races(years):
        try:
            r = load(path)
        except Exception as e:  # a session the archive can't replay (no timing): skip it
            print(f'skip {label}: {e}')
            continue
        if r['total'] and len(r['laps']) > 100:
            rs.append({**r, 'label': label})
    if len(rs) < 3:
        print(f'{name}: only {len(rs)} races, not fitted')
        return None
    sums = [summary(r) for r in rs]
    cal = calibrate(sums)
    print(f'\n== era {name}: {len(rs)} races/sprints from {years[0]}-{years[-1]}\n', json.dumps(cal))
    rows = []
    for i, r in enumerate(rs):
        loo = calibrate(sums[:i] + sums[i + 1:])
        base, new = score(r, r['m0']), score(r, {**r['m0'], **loo})
        rows.append((r['label'], base, new))
        print(f"{r['label']:<38} " + '  '.join(f"{k} {base[k]:.3f}->{new[k]:.3f}" for k in base))
    for label, i in (('default', 1), ('calibrated', 2)):
        print(f'{label:>10}: ' + '  '.join(f"{k} {np.nanmean([row[i][k] for row in rows]):.3f}" for k in rows[0][1]))
    return cal, [r['label'] for r in rs], rows


def main(argv):
    write = '--write' in argv
    now = data.datetime.now(data.timezone.utc).year
    asked = {int(a) for a in argv if a.isdigit()}
    eras = {sim.era(y) for y in asked} if asked else set(sim.ERAS)
    try:
        saved = json.loads(OUT.read_text()).get('eras', {})
    except (FileNotFoundError, ValueError, AttributeError):
        saved = {}
    for name in sim.ERAS:
        if name not in eras:
            continue
        a, b = sim.ERAS[name]
        years = [y for y in range(max(a, data.FIRST_ARCHIVE_YEAR), min(b, now) + 1) if not asked or y in asked]
        got = fit_era(name, years)
        if got:
            saved[name] = {'_fitted_on': got[1], **got[0]}
    if write:
        OUT.write_text(json.dumps({'eras': saved}, indent=1))
        print('wrote', OUT)


if __name__ == '__main__':
    main(sys.argv[1:])
