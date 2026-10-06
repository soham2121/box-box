"""Strategy sim: fit a lap-time model on clean laps, Monte Carlo the rest of the race, answer pit what-ifs.

lap time = base[driver] + offset[compound] + deg[compound] * tyre_age + fuel_s_per_kg * fuel(lap) + noise
Fuel and tyre age both grow linearly through a stint, so data can't separate them: fuel is a fixed knob, deg is fit.
"""
import sys

import numpy as np
from scipy.optimize import lsq_linear

MODEL = {  # ponytail: hand-tuned defaults. Every value is a calibration knob (config.json "model" overrides them)
    'fuel_start_kg': 75.0,    # 2026 fuel is energy-limited (3000 MJ/h); teams don't publish loads
    'fuel_s_per_kg': 0.03,    # lap time cost per kg
    'min_mass_kg': 768.0,     # 2026 minimum mass incl. driver, without fuel
    'pit_loss': 22.0,         # used when the circuit API has no value
    'sc_prob': 0.35,          # chance of a (V)SC per race, spread evenly over laps
    'sc_laps': 4,
    'sc_lap_factor': 1.35,
    'pass_threshold': 0.8,    # s/lap a car needs over the one ahead to get past in one lap
    'min_gap': 0.4,
    'noise_min': 0.25,
    'prior_weight': 3.0, 'half_life_laps': 4, 'trend_stiffness': 300,      # pseudo-laps pulling compound params towards the prior
    'prior': {'SOFT': (-0.5, 0.10), 'MEDIUM': (0.0, 0.06), 'HARD': (0.4, 0.04),  # (offset vs MEDIUM s, deg s/lap)
              'INTERMEDIATE': (6.0, 0.05), 'WET': (10.0, 0.05)},
    'life': {'SOFT': 18, 'MEDIUM': 28, 'HARD': 40, 'INTERMEDIATE': 99, 'WET': 99},
}


def fuel_kg(lap, total, m):
    return m['fuel_start_kg'] * max(0.0, 1 - lap / max(total, 1))


def mass_kg(lap, total, m=MODEL):
    return m['min_mass_kg'] + fuel_kg(lap, total, m)


def clean(laps, m=MODEL):
    """Green-flag, non-pit laps on a known compound, without traffic or obvious mistakes."""
    ok = [l for l in laps if l['t'] and not l['pit'] and not l['sc'] and l['lap'] > 1 and l['cmp'] in m['prior']]
    if not ok:
        return []
    med = np.median([l['t'] for l in ok])
    return [l for l in ok if l['t'] < med * 1.07 and (l['int'] is None or l['int'] > 1.0)]


def fit(laps, total, m=MODEL):
    """Least squares with priors. Params: base per driver, offset + deg per compound, one per-lap trend.
    The trend soaks up fuel burn + track evolution; it separates from tyre age once pit stops reset the age."""
    L, cmps = clean(laps, m), list(m['prior'])
    drvs = sorted({l['drv'] for l in L})
    D, C = len(drvs), len(cmps)
    P = D + 2 * C + 1
    rows, y = [], []
    last = max((l['lap'] for l in L), default=0)
    for l in L:
        r = np.zeros(P)
        c = cmps.index(l['cmp'])
        r[drvs.index(l['drv'])], r[D + c], r[D + C + c], r[-1] = 1, 1, l['age'], l['lap']
        k = 0.5 ** ((last - l['lap']) / (2 * m['half_life_laps']))  # sqrt of the recency weight: drying tracks, early trains
        rows.append(r * k)
        y.append(l['t'] * k)
    w = np.sqrt(m['prior_weight'])
    trend = -m['fuel_s_per_kg'] * m['fuel_start_kg'] / max(total, 1)  # fuel burn alone
    priors = [(D + c, off, 1) for c, (off, _) in enumerate(m['prior'].values())] +              [(D + C + c, deg, 10) for c, (_, deg) in enumerate(m['prior'].values())] + [(P - 1, trend, m['trend_stiffness'])]
    for col, val, scale in priors:  # pseudo-observations: ridge towards the prior (scale = how stiff)
        r = np.zeros(P)
        r[col] = w * scale
        rows.append(r)
        y.append(w * scale * val)
    lo = np.full(P, -np.inf)
    lo[D + C:D + 2 * C] = 0  # tyres don't get faster with age; negative fits are track evolution leaking in
    p = lsq_linear(np.array(rows), np.array(y), bounds=(lo, np.inf)).x
    res = np.array([l['t'] for l in L]) - np.array([predict_row(l, drvs, cmps, p) for l in L])
    sig = {d: max(m['noise_min'], float(np.std(res[[l['drv'] == d for l in L]]))) for d in drvs}
    return {'base': dict(zip(drvs, p[:D])), 'off': dict(zip(cmps, p[D:D + C])), 'deg': dict(zip(cmps, p[D + C:D + 2 * C])),
            'trend': float(p[-1]), 'sigma': sig, 'total': total, 'm': m, 'n': len(L)}


def predict_row(l, drvs, cmps, p):
    D, C = len(drvs), len(cmps)
    c = cmps.index(l['cmp'])
    return p[drvs.index(l['drv'])] + p[D + c] + p[D + C + c] * l['age'] + p[-1] * l['lap']


def _base(model, drv):
    """Drivers without clean laps yet get the field average plus half a second."""
    b = model['base']
    return b[drv] if drv in b else (np.mean(list(b.values())) + 0.5 if b else 90.0)


def predict(model, drv, lap, cmp, age):
    return _base(model, drv) + model['off'].get(cmp, 0) + model['deg'].get(cmp, 0.05) * age + model['trend'] * lap


def priors_from(laps, m=MODEL):
    """Compound offsets/deg from practice long runs -> a 'prior' dict for the race (FP fuel loads are unknown)."""
    long_runs = [l for l in clean(laps, m) if sum(1 for o in laps if o['drv'] == l['drv'] and o['cmp'] == l['cmp']) >= 6]
    fp = fit(long_runs, 1, {**m, 'fuel_s_per_kg': 0.0, 'half_life_laps': 1e9})  # lap numbers restart per session
    seen = {l['cmp'] for l in long_runs}
    return {c: ((fp['off'][c] - fp['off'].get('MEDIUM', 0), fp['deg'][c]) if c in seen else v) for c, v in m['prior'].items()}


def pit_loss_from(laps, default):
    """Median (in-lap + out-lap - 2 normal laps) over green-flag stops; for tracks the circuit API doesn't cover."""
    by = {(l['drv'], l['lap']): l for l in laps}
    norm = {}
    for l in clean(laps):
        norm.setdefault(l['drv'], []).append(l['t'])
    out = []
    for l in laps:
        nxt = by.get((l['drv'], l['lap'] + 1))
        if l['pit'] and l['lap'] > 1 and nxt and nxt['pit'] and l['t'] and nxt['t'] and not (l['sc'] or nxt['sc'])                 and norm.get(l['drv']):
            out.append(l['t'] + nxt['t'] - 2 * np.median(norm[l['drv']]))
    return float(np.median(out)) if out else default


def gap_s(d, lap_s=95.0):
    g = str(d['gap'])
    if 'L' in g:  # '1 L' / 'LAP 12' (leader)
        return 0.0 if g.startswith('LAP') else lap_s * float(g.split()[0])
    try:
        return float(g.lstrip('+') or 0)
    except ValueError:
        return d['pos'] * 0.5


def simulate(drivers, lap, model, plan=None, runs=500, seed=0, pit_loss=None):
    """Monte Carlo from the current running order to the flag.
    drivers: State.drivers() rows. plan: (drv, pit_lap, compound) forced for one driver.
    Returns {'drv': [...], 'code': [...], 'pos': (runs, D) finishing positions}."""
    m, total, rng = model['m'], model['total'], np.random.default_rng(seed)
    ds = [d for d in drivers if not d['out']]
    D, R, cmps = len(ds), runs, list(m['prior'])
    loss = pit_loss or {'normal': m['pit_loss'], 'sc': m['pit_loss'] * 0.6}
    lap_s = np.median([predict(model, d['drv'], lap, 'MEDIUM', 0) for d in ds])
    ci = lambda c: cmps.index(c) if c in cmps else cmps.index('MEDIUM')
    base = np.array([_base(model, d['drv']) for d in ds])
    off = np.array([model['off'].get(c, 0) for c in cmps])
    deg = np.maximum([model['deg'].get(c, 0.05) for c in cmps], 0)  # tyres never get faster with age
    life = np.array([m['life'].get(c, 30) for c in cmps])
    sig = np.array([model['sigma'].get(d['drv'], 0.5) for d in ds])

    T = np.tile([gap_s(d, lap_s) for d in ds], (R, 1))
    cmp = np.tile([ci(d['cmp']) for d in ds], (R, 1))
    age = np.tile([float(d['age']) for d in ds], (R, 1))
    used = np.zeros((R, D, len(cmps)), bool)
    for j, d in enumerate(ds):
        used[:, j, [ci(c) for c in d['used'] if c]] = True
        used[:, j, ci(d['cmp'])] = True
    wet = cmp >= cmps.index('INTERMEDIATE')
    pj = next((j for j, d in enumerate(ds) if plan and d['drv'] == plan[0]), None)
    sc_left = np.zeros(R, int)
    hazard = 1 - (1 - m['sc_prob']) ** (1 / max(total, 1))

    for L in range(lap + 1, total + 1):
        left = total - L
        new_sc = (sc_left == 0) & (rng.random(R) < hazard) & (left > 3)
        if new_sc.any():  # field bunches up behind the safety car
            order = np.argsort(T[new_sc], axis=1)
            ranks = np.argsort(order, axis=1)
            T[new_sc] = np.take_along_axis(T[new_sc], order[:, :1], 1) + ranks * 0.8
        sc_left[new_sc] = m['sc_laps']
        sc = sc_left > 0

        two = used.sum(2) >= 2
        want = (age >= life[cmp]) & (left > 3) | (~two & (left <= 4)) | (sc[:, None] & (age > 0.4 * life[cmp]) & (left > 8))
        want &= ~wet
        if pj is not None:
            want[:, pj] = L == plan[1]
        choice = ci('SOFT') if left <= life[ci('SOFT')] else ci('MEDIUM') if left <= life[ci('MEDIUM')] else ci('HARD')
        new_c = np.where(~two & (cmp == choice), np.where(cmp == ci('MEDIUM'), ci('HARD'), ci('MEDIUM')), choice)
        if pj is not None:
            new_c[:, pj] = ci(plan[2])
        cmp = np.where(want, new_c, cmp)
        age = np.where(want, 0, age + 1)
        used[want, cmp[want]] = True

        lt = base + off[cmp] + deg[cmp] * age + model['trend'] * L + rng.standard_normal((R, D)) * sig
        lt = np.where(sc[:, None], lap_s * m['sc_lap_factor'], lt)
        lt += want * np.where(sc, loss.get('sc', loss['normal']), loss['normal'])[:, None]

        order = np.argsort(T, axis=1)  # ponytail: one pass of "can't pass unless much faster", not a real traffic model
        nt = np.take_along_axis(T + lt, order, 1)
        lts, pit = np.take_along_axis(lt, order, 1), np.take_along_axis(want, order, 1)
        for i in range(1, D):
            held = (nt[:, i] < nt[:, i - 1] + m['min_gap']) & ~pit[:, i] & ~pit[:, i - 1] & \
                   (sc | (lts[:, i - 1] - lts[:, i] < m['pass_threshold']))
            nt[:, i] = np.where(held, nt[:, i - 1] + m['min_gap'], nt[:, i])
        np.put_along_axis(T, order, nt, 1)
        sc_left = np.maximum(sc_left - 1, 0)

    return {'drv': [d['drv'] for d in ds], 'code': [d['code'] for d in ds],
            'pos': np.argsort(np.argsort(T, axis=1), axis=1) + 1}


def rejoin(drivers, drv, loss):
    """Where drv comes out if they pit now: (position, car ahead, gap ahead, car behind, gap behind)."""
    lap_s = 95.0
    g = {d['drv']: (gap_s(d, lap_s), d['code']) for d in drivers if not d['out']}
    me = g[drv][0] + loss
    others = sorted((v, c) for k, (v, c) in g.items() if k != drv)
    ahead = [o for o in others if o[0] <= me]
    behind = [o for o in others if o[0] > me]
    return (len(ahead) + 1, ahead[-1][1] if ahead else None, me - ahead[-1][0] if ahead else None,
            behind[0][1] if behind else None, behind[0][0] - me if behind else None)


def what_if(drivers, lap, model, drv, pit_lap, compound, runs=400, pit_loss=None):
    """Same random numbers for both runs, so the difference is the strategy, not the dice."""
    a = simulate(drivers, lap, model, None, runs, 1, pit_loss)
    b = simulate(drivers, lap, model, (drv, pit_lap, compound), runs, 1, pit_loss)
    j = a['drv'].index(drv)
    return {'base': a['pos'][:, j], 'plan': b['pos'][:, j]}


def best_window(drivers, lap, model, drv, runs=150, pit_loss=None, compounds=('MEDIUM', 'HARD', 'SOFT')):
    """Mean finishing position for every (pit lap, compound) in the next 25 laps; best first."""
    out = []
    for c in compounds:
        for L in range(lap + 1, min(lap + 26, model['total'] - 1)):
            r = simulate(drivers, lap, model, (drv, L, c), runs, 1, pit_loss)
            out.append((float(r['pos'][:, r['drv'].index(drv)].mean()), L, c))
    return sorted(out)


def snapshot(laps, lap):
    """Rebuild State.drivers()-like rows as they were at the end of `lap` (backtests)."""
    rows = []
    for drv in {l['drv'] for l in laps}:
        mine = [l for l in laps if l['drv'] == drv and l['lap'] <= lap]
        if mine and mine[-1]['lap'] == lap:
            last = mine[-1]
            rows.append({'drv': drv, 'code': drv, 'pos': last['pos'], 'out': False, 'cmp': last['cmp'], 'age': last['age'],
                         'gap': f"+{last['gap']}" if last['gap'] is not None else ('' if last['pos'] == 1 else '1 L'),
                         'used': {l['cmp'] for l in mine if l['lap'] > 1}})
    return sorted(rows, key=lambda d: d['pos'])


def backtest(path, fit_to=20):
    from .live import laps_from_archive
    st, laps = laps_from_archive(path)
    total = st.session()['total']
    loss = (st.track.pit_loss if st.track else None) or {'normal': pit_loss_from(laps, MODEL['pit_loss'])}
    model = fit([l for l in laps if l['lap'] <= fit_to], total)

    # 1) lap-time prediction on later clean laps of drivers still on their lap-`fit_to` tyres
    stint_at = {l['drv']: l['cmp'] for l in laps if l['lap'] == fit_to}
    pitted, ok, err = {}, {id(l) for l in clean(laps)}, []
    for l in sorted(laps, key=lambda l: l['lap']):
        if l['lap'] > fit_to and l['pit']:
            pitted[l['drv']] = True
        if l['lap'] > fit_to and not pitted.get(l['drv']) and id(l) in ok and stint_at.get(l['drv']) == l['cmp']:
            err.append(abs(predict(model, l['drv'], l['lap'], l['cmp'], l['age']) - l['t']))
    mae = float(np.mean(err)) if err else float('nan')

    # 2) "pit now" rejoin position vs where they really were after the out-lap (green-flag stops only)
    by = {(l['drv'], l['lap']): l for l in laps}
    rj = []
    for l in laps:
        prev, out = by.get((l['drv'], l['lap'] - 1)), by.get((l['drv'], l['lap'] + 1))
        if l['pit'] and l['lap'] > 2 and prev and out and not (l['sc'] or prev['sc'] or out['sc']) and not prev['pit']:
            pred = rejoin(snapshot(laps, l['lap'] - 1), l['drv'], loss['normal'])[0]
            rj.append(abs(pred - out['pos']))

    # 3) Monte Carlo from lap `fit_to` vs the real finishing order
    snap = snapshot(laps, fit_to)
    sim = simulate(snap, fit_to, model, runs=300, pit_loss=loss)
    final = {l['drv']: l['pos'] for l in laps if l['lap'] == max(o['lap'] for o in laps if o['drv'] == l['drv'])}
    pred = sim['pos'].mean(0)
    real = np.array([final.get(d, 20) for d in sim['drv']])
    rho = float(np.corrcoef(np.argsort(np.argsort(pred)), np.argsort(np.argsort(real)))[0, 1])
    return {'clean_laps_fit': model['n'], 'lap_mae_s': mae, 'n_err': len(err),
            'rejoin_median_err': float(np.median(rj)) if rj else float('nan'), 'n_stops': len(rj),
            'finish_rank_corr': rho, 'pit_loss': round(loss['normal'], 1), 'trend': round(model['trend'], 3),
            'deg': {c: round(float(v), 3) for c, v in model['deg'].items() if c in ('SOFT', 'MEDIUM', 'HARD')}}


if __name__ == '__main__':
    path = sys.argv[1] if len(sys.argv) > 1 else '2026/2026-09-26_Azerbaijan_Grand_Prix/2026-09-26_Race/'
    r = backtest(path)
    print(r)
    assert r['lap_mae_s'] < 0.5, 'lap-time model worse than plan target'
    assert r['rejoin_median_err'] <= 1, 'pit rejoin estimate off by more than one place'
    assert r['finish_rank_corr'] > 0.6, 'race sim does not track the real finishing order'
    print('backtest OK')
