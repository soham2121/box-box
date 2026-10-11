"""Strategy sim: fit a lap-time model on clean laps, Monte Carlo the rest of the race, answer pit what-ifs.

lap time = base[driver] + offset[compound] + deg[compound] * tyre_age + fuel_s_per_kg * fuel(lap) + noise
Fuel and tyre age both grow linearly through a stint, so data can't separate them: fuel is a fixed knob, deg is fit.
"""
import copy
import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np
from scipy.optimize import lsq_linear

MODEL = {  # ponytail: hand-tuned defaults. Every value is a calibration knob (config.json "model" overrides them)
    'fuel_start_kg': 70.0,    # per season: SEASONS below
    'fuel_s_per_kg': 0.03,    # lap time cost per kg
    'min_mass_kg': 768.0,     # per season: SEASONS below
    'pit_loss': 22.0,         # last resort when neither the circuit API, the session nor the circuit catalogue has a value
    'sc_pit_ratio': 0.634,    # SC / VSC pit loss as a share of the green one: MultiViewer's ratios, identical at all 32
    'vsc_pit_ratio': 0.723,   # circuits in its catalogue. Only used when MultiViewer has no entry for the circuit
    'neutral_lap_ratio': 1.15,  # a lap whose field median is this much slower than the session's is not green (SC starts,
                                # red-flag restarts). Measured: drying-track green laps <= 1.10x, SC laps >= 1.16x
    'sc_per_race': 0.43,      # mean safety cars per race, spread evenly over laps (0.43 = a 35 % chance of at least one)
    'sc_laps': 4,
    'sc_lap_factor': 1.35,
    'vsc_per_race': 0.0,      # virtual safety cars: same idea, gaps frozen instead of bunched
    'vsc_laps': 2,
    'vsc_lap_factor': 1.3,
    'pass_threshold': 0.8,    # s/lap a car needs over the one ahead to get past in one lap (when 'pass' is None)
    'pass': None,             # [a, b, c]: P(a car within 1 s gets past in a lap) = sigmoid(a + b*pace edge + c*gap), calibrate.py
    'pass_restart': None,     # same, for the laps after a restart while DRS / Overtake Mode is still off (calibrate.py)
    'restart_laps': 1,        # racing laps after an SC / red-flag restart before DRS / Overtake Mode: per season (SEASONS)
    'red_per_race': 0.0,      # red flags per race: free tyre change for everyone (it counts for the two-compound rule)
    'pit_neutral': None,      # [w...]: P(a car pits on an SC/VSC lap), calibrate.py; None: worn tyres (40 % of life) stop
    'pit_neutral_sd': None,   # {'sc', 'vsc'}: spread of a factor shared by all cars in one SC / VSC (all pit or all stay out)
    'min_gap': 0.4,
    'noise_min': 0.25,
    'prior_weight': 3.0,      # pseudo-laps pulling compound params towards the prior
    'half_life_laps': 8, 'trend_stiffness': 30,  # leave-one-race-out on 2025 and 2026: better order and lap times than 4 / 300
    'pace_sd': 0.0,           # s/lap: each run draws every driver's true pace this far from the fit (it is an estimate);
                              # calibrate.py fits it per era so 80 % of real finishes land in the sim's 80 % range
    'prior': {'SOFT': (-0.5, 0.10), 'MEDIUM': (0.0, 0.06), 'HARD': (0.4, 0.04),  # (offset vs MEDIUM s, deg s/lap)
              'INTERMEDIATE': (6.0, 0.05), 'WET': (10.0, 0.05)},
    'life': {'SOFT': 18, 'MEDIUM': 28, 'HARD': 40, 'INTERMEDIATE': 99, 'WET': 99},
    'slick_wet_s': 6.0,       # s/lap slicks lose to inters on a wet track: median of 69 laps in the 5 mixed races of
                              # 2025-26 (quartiles -1 / +16: it depends on how wet, a drying track favours slicks)
    'two_compounds': True,    # Grand Prix rule; the sim panel and calibrate.py turn it off for sprints (free tyre choice)
}
DEFAULTS = copy.deepcopy(MODEL)  # before calibration: calibrate.py's baseline

# Each regulation era is a different car (aero, tyres, power unit, overtaking aid), so everything fitted is per era
ERAS = {'2018-2021': (2018, 2021),  # 2017-spec wide cars, DRS (the live timing archive starts in 2018)
        '2022-2025': (2022, 2025),  # ground-effect cars, DRS
        '2026-': (2026, 9999)}      # new power units, active aero, Overtake Mode instead of DRS
# Regulation facts per season: minimum mass incl. driver without fuel (kg), typical race start fuel (kg). The fuel
# limit was 105 kg in 2018 and 110 kg from 2019 (about 100 used); 2026 limits energy (3000 MJ/h), ~70 kg reported.
SEASONS = {2018: (733, 105), 2019: (743, 100), 2020: (746, 100), 2021: (752, 100), 2022: (798, 100),
           2023: (798, 100), 2024: (798, 100), 2025: (800, 100), 2026: (768, 70)}


def era(year):
    return next((k for k, (a, b) in ERAS.items() if a <= year <= b), '2026-')


try:  # {era: knobs} fitted on that era's races by calibrate.py
    CALIBRATION = json.loads(Path(__file__).with_name('calibration.json').read_text()).get('eras', {})
except FileNotFoundError:
    CALIBRATION = {}
except (OSError, ValueError, AttributeError) as e:
    print('calibration.json unreadable, using defaults:', e)
    CALIBRATION = {}


def season_model(year, overrides=None, calibrated=True):
    """Knobs for a season: defaults < that season's regulation facts < its era's calibration < config overrides."""
    mass, fuel = SEASONS.get(year, SEASONS[max(SEASONS)] if year > max(SEASONS) else SEASONS[min(SEASONS)])
    cal = {k: v for k, v in CALIBRATION.get(era(year), {}).items() if k in DEFAULTS} if calibrated else {}
    if 'prior' in cal:  # JSON lists back to (offset, deg) pairs
        cal['prior'] = {c: tuple(v) for c, v in cal['prior'].items()}
    restart = 2 if year <= 2023 else 1  # DRS after an SC restart: 2 racing laps until 2023, 1 from 2024; 2026 Overtake
    # Mode: once every car has passed the detection point after the restart, so about 1 lap
    m = {**copy.deepcopy(DEFAULTS), 'min_mass_kg': float(mass), 'fuel_start_kg': float(fuel), 'restart_laps': restart, **cal}
    for k, v in (overrides or {}).items():  # a table ('life') is overridden entry by entry: soft life alone keeps the rest
        m[k] = {**m[k], **v} if isinstance(v, dict) and isinstance(m.get(k), dict) else v
    return m


MODEL.update(season_model(max(SEASONS)))  # current era: the default wherever no session year is known


SOFTNESS = ['HYPERSOFT', 'ULTRASOFT', 'SUPERSOFT', 'SOFT', 'MEDIUM', 'HARD', 'SUPERHARD']  # Pirelli 2018, softest first


def relative_compounds(laps):
    """{2018 name: SOFT/MEDIUM/HARD}. From 2019 the feed names the softest / middle / hardest of a race's three
    compounds SOFT / MEDIUM / HARD; 2018 sent Pirelli's seven names, so a 2018 race is relabelled the same way to fit
    one model. ponytail: a race that ran only two of its three maps them to SOFT / MEDIUM (the third is unknown)."""
    seen = sorted({l['cmp'] for l in laps if l['cmp'] in SOFTNESS}, key=SOFTNESS.index)
    if set(seen) <= {'SOFT', 'MEDIUM', 'HARD'}:
        return {}
    return dict(zip(seen, ('SOFT', 'MEDIUM', 'HARD') if len(seen) >= 3 else ('SOFT', 'MEDIUM') if len(seen) == 2 else ('MEDIUM',)))


def relabel(rows, mapping):
    """Laps or State.drivers() rows with compounds renamed by relative_compounds()."""
    if not mapping:
        return rows
    return [{**r, 'cmp': mapping.get(r['cmp'], r['cmp']),
             **({'used': {mapping.get(c, c) for c in r['used']}} if 'used' in r else {})} for r in rows]


def fuel_kg(lap, total, m):
    return m['fuel_start_kg'] * max(0.0, 1 - lap / max(total, 1))


def mass_kg(lap, total, m=MODEL):
    return m['min_mass_kg'] + fuel_kg(lap, total, m)


def clean(laps, m=MODEL):
    """Green-flag, non-pit laps on a known compound, without obvious mistakes, and without traffic (within 1 s of the
    car ahead) unless that would leave a driver fewer than 3 laps: Bahrain 2026, HAM ran in traffic from lap 13 on and
    his only traffic-free laps were laps 7-8, 10 s slower, so the sim put him from P9 to P21."""
    ok = [l for l in laps if l['t'] and not l['pit'] and not l['sc'] and l['lap'] > 1 and l['cmp'] in m['prior']]
    if not ok:
        return []
    med = np.median([l['t'] for l in ok])
    fast = [l for l in ok if l['t'] < med * 1.07]
    free = lambda l: l['int'] is None or l['int'] > 1.0
    n = Counter(l['drv'] for l in fast if free(l))
    return [l for l in fast if free(l) or n[l['drv']] < 3]


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
    # no clean laps yet (a start behind the SC, a wet start): the field's level from any laps, SC laps scaled to green
    seen = [(l['t'] / (m['sc_lap_factor'] if l['sc'] else 1)) - p[D + cmps.index(l['cmp'])] - p[-1] * l['lap']
            for l in laps if l['t'] and not l['pit'] and l['lap'] > 1 and l['cmp'] in cmps]
    return {'base': dict(zip(drvs, p[:D])), 'off': dict(zip(cmps, p[D:D + C])), 'deg': dict(zip(cmps, p[D + C:D + 2 * C])),
            'trend': float(p[-1]), 'sigma': sig, 'total': total, 'm': m, 'n': len(L),
            'level': float(np.median(seen)) if seen else 90.0}


def predict_row(l, drvs, cmps, p):
    D, C = len(drvs), len(cmps)
    c = cmps.index(l['cmp'])
    return p[drvs.index(l['drv'])] + p[D + c] + p[D + C + c] * l['age'] + p[-1] * l['lap']


def _base(model, drv):
    """Drivers without clean laps yet get the field average plus half a second (no clean laps at all: the level
    of whatever laps there are)."""
    b = model['base']
    return b[drv] if drv in b else (np.mean(list(b.values())) + 0.5 if b else model.get('level', 90.0))


def predict(model, drv, lap, cmp, age):
    return _base(model, drv) + model['off'].get(cmp, 0) + model['deg'].get(cmp, 0.05) * age + model['trend'] * lap


def stints(laps, min_laps=5):
    """Practice long runs: >= min_laps consecutive laps (lap and tyre age both +1) on one set, cool-down laps dropped.
    Pairing quali-sim laps with race runs on the same compound would read the fuel difference as tyre wear."""
    runs, cur = [], []
    for l in sorted((l for l in laps if l['t'] and not l['pit'] and not l['sc'] and l['lap'] > 1 and l['cmp'] in MODEL['life']),
                    key=lambda l: (l['drv'], l.get('sess', 0), l['lap'])):
        p = cur[-1] if cur else None
        if p and (p['drv'], p.get('sess', 0), p['cmp'], p['lap'] + 1, p['age'] + 1) == (l['drv'], l.get('sess', 0), l['cmp'], l['lap'], l['age']):
            cur.append(l)
        else:
            runs.append(cur)
            cur = [l]
    runs.append(cur)
    out = []
    for r in runs:
        best = min((l['t'] for l in r), default=0)
        r = [l for l in r if l['t'] < best * 1.04]  # push/cool-down alternation leaves too few laps and drops out
        if len(r) >= min_laps:
            out.append(r)
    return out


def priors_from(laps, total, m=MODEL):
    """Tyre deg per compound from practice long runs -> a 'prior' dict for the race. Each run gets its own intercept
    (fuel load, track state), so only the slope within a run is used. Fuel burns during a run too, so the slope is
    corrected with the same fuel knobs the race model uses. Compound offsets keep the model prior: unknown FP fuel
    loads make them unrecoverable. A compound needs two runs."""
    runs, deg = stints(laps), {}
    for c in m['prior']:
        rs = [r for r in runs if r[0]['cmp'] == c]
        if len(rs) < 2:
            continue
        x = np.concatenate([[l['age'] - np.mean([o['age'] for o in r]) for l in r] for r in rs])
        y = np.concatenate([[l['t'] - np.mean([o['t'] for o in r]) for l in r] for r in rs])
        slope = float(x @ y / (x @ x)) if x @ x else 0.0
        deg[c] = max(0.0, slope + m['fuel_s_per_kg'] * m['fuel_start_kg'] / max(total, 1))
    return {c: (off, deg.get(c, d)) for c, (off, d) in m['prior'].items()}


def stops_from_laps(laps):
    """Stops when PitStopSeries isn't flowing: in-lap n and out-lap n+1 both pit-flagged. Lap 1 never counts
    (cars leave the pit lane for the grid and pit-lane starters are not stops)."""
    flagged = {(l['drv'], l['lap']) for l in laps if l['pit']}
    return [{'drv': d, 'lap': n} for d, n in sorted(flagged) if n >= 2 and (d, n + 1) in flagged and (d, n - 1) not in flagged]


def pit_loss_samples(laps, stops, m=MODEL):
    """Time lost by each green-flag stop. Preferred: in-lap and out-lap against the driver's own laps either side, minus
    how much the field's lap time moved over the same laps. Otherwise: against the field on those same two laps,
    corrected for the driver's pace. Both cancel track evolution, fuel and weather. Over the 13 races of 2026 that
    MultiViewer covers, the session median is within 1.5 s of its value on average.
    stops: [{'drv', 'lap', 'stationary'?}] with lap = the in-lap (PitStopSeries 'Lap')."""
    by = {(l['drv'], l['lap']): l for l in laps}
    pitted = {(s['drv'], s['lap']) for s in stops} | {(s['drv'], s['lap'] + 1) for s in stops}
    field = {}
    for l in laps:
        if l['t'] and l['lap'] > 1 and (l['drv'], l['lap']) not in pitted:
            field.setdefault(l['lap'], []).append(l['t'])
    field = {n: float(np.median(v)) for n, v in field.items() if len(v) >= 3}
    if not field:
        return []
    base = np.median(list(field.values()))
    neutral = {n for n, v in field.items() if v > m['neutral_lap_ratio'] * base} | {l['lap'] for l in laps if l['sc']}
    pace = {}
    for l in laps:
        n = l['lap']
        if l['t'] and n in field and n not in neutral and (l['drv'], n) not in pitted:
            pace.setdefault(l['drv'], []).append(l['t'] - field[n])
    # a 12 s wheel-nut problem or a stop-and-go is not the pit loss of the next stop: swap in the typical stationary time
    stat = [s['stationary'] for s in stops if s.get('stationary')]
    typical_stat = float(np.median(stat)) if stat else None
    out = []
    for s in stops:
        n, a, b = s['lap'], by.get((s['drv'], s['lap'])), by.get((s['drv'], s['lap'] + 1))
        if n < 2 or not (a and b and a['t'] and b['t']) or {n, n + 1} & neutral or not {n, n + 1} <= set(field):
            continue
        extra = s['stationary'] - typical_stat if s.get('stationary') and typical_stat else 0.0
        p, q = by.get((s['drv'], n - 1)), by.get((s['drv'], n + 2))
        if (n >= 3 and p and q and p['t'] and q['t'] and not {n - 1, n + 2} & neutral and {n - 1, n + 2} <= set(field)
                and (s['drv'], n - 1) not in pitted and (s['drv'], n + 2) not in pitted):
            out.append(a['t'] - p['t'] + b['t'] - q['t'] - (field[n] - field[n - 1]) - (field[n + 1] - field[n + 2]) - extra)
        else:
            own = float(np.median(pace[s['drv']])) if len(pace.get(s['drv'], ())) >= 3 else 0.0
            out.append(a['t'] + b['t'] - field[n] - field[n + 1] - 2 * own - extra)
    out = np.array([x for x in out if x > 0])
    if len(out) >= 3:  # drop stop-and-go penalties and botched stops
        med, mad = np.median(out), np.median(np.abs(out - np.median(out))) * 1.4826
        out = out[np.abs(out - med) <= 3 * max(mad, 0.5)]
    return [float(x) for x in out]


def pit_loss(laps=(), stops=None, known=None, weekend=None, typical=None, m=MODEL):
    """Pit loss {'normal', 'sc', 'vsc', 'source', 'n'} from the best available source:
    this session's green stops (>= 3) > circuit API value > an earlier race of the weekend (sprint)
    > median over the circuit catalogue > MODEL default. The session's own stops beat the published value as soon as
    there are 3 (scripts/validate_pit_blend.py, 799 stops of 2025-26: loss 1.18 -> 0.84 s off, 'pit now' rejoin
    1.35 -> 1.30 places off; Singapore is published at 27.6 s and measured 23.6-24.2 s). SC / VSC: the published
    values while there are any (too few stops under them to measure)."""
    s = pit_loss_samples(laps, stops if stops is not None else stops_from_laps(laps), m)
    if len(s) >= 3:  # two samples were up to 15 s off in the 2026 races
        normal, src, n = float(np.median(s)), 'this session', len(s)
    elif known and known.get('normal'):
        normal, src, n = float(known['normal']), 'circuit data', 0
    elif weekend:
        normal, src, n = weekend['normal'], weekend['source'], weekend['n']
    elif typical:
        normal, src, n = typical, 'typical circuit', 0
    else:
        normal, src, n = m['pit_loss'], 'default', 0
    known = known or {}
    return {'normal': normal, 'sc': float(known.get('sc') or normal * m['sc_pit_ratio']),
            'vsc': float(known.get('vsc') or normal * m['vsc_pit_ratio']), 'source': src, 'n': n}


def lap_time(laps):
    """A green lap at the front right now: the leader's median over their last 5 green laps (lapped cars' gaps
    and the 'pit now' rejoin need it), or None before there are two."""
    lead = [l['t'] for l in laps if l['pos'] == 1 and l['t'] and not l['pit'] and not l['sc'] and l['lap'] > 1][-5:]
    return float(np.median(lead)) if len(lead) >= 2 else None


def _num(s):
    try:
        return float(str(s).lstrip('+'))
    except ValueError:
        return None


def gaps(drivers, lap_s=None):
    """{drv: seconds behind the leader}. A lapped car's gap reads '1L' / '6L', but its interval to the car ahead is
    usually still in seconds, so it's the car ahead's gap plus that interval; whole laps of lap_s (95 s when unknown)
    only when the interval is in laps too. Counting '1L' as exactly one lap put cars a lap+ down too close."""
    out, prev = {}, None
    for d in sorted(drivers, key=lambda d: d['pos']):
        g = str(d['gap']).strip()
        v = _num(g)
        if v is None and prev is not None and g[:1].isdigit() and 'L' in g:
            iv = _num(d.get('int', ''))
            v = prev + iv if iv is not None else (lap_s or 95.0) * float(g.split('L')[0])
        elif v is None:  # leader ('LAP 12' / ''), or no gap yet
            v = 0.0 if prev is None else prev + 0.5
        out[d['drv']] = prev = v
    return out


def track_now(drivers):
    """'DRY' or 'WET' from what most running cars are on. The feed has no track-wetness value (WeatherData Rainfall
    is only rain at the weather station), and the teams' tyre calls are the best read of the track."""
    on = [d['cmp'] for d in drivers if not d['out']]
    return 'WET' if sum(c in ('INTERMEDIATE', 'WET') for c in on) * 2 > len(on) else 'DRY'


NEUTRAL = ('SC', 'VSC', 'RED')


def pit_neutral_features(age_share, left_share, owes, ep_lap, vsc):
    """Inputs of the 'pit_neutral' odds (calibrate.py fits them): tyre age as a share of its default life, share of the
    race left, still owes the two-compound rule, first / second lap of the SC or VSC, and whether it's a VSC.
    (The gap to the car behind vs the SC pit loss was tried too: no better on 2026's races.)"""
    return [1.0, age_share, left_share, owes * 1.0, (ep_lap == 0) * 1.0, (ep_lap == 1) * 1.0, vsc * 1.0]


def simulate(drivers, lap, model, plan=None, runs=500, seed=0, pit_loss=None, lap_s=None, conditions=None,
             neutral_now=None, events=None):
    """Monte Carlo from the current running order to the flag.
    drivers: State.drivers() rows. plan: (drv, pit_lap, compound) forced for one driver.
    lap_s: lap_time() of the session; the model's prediction when None.
    conditions: track from here on, 'DRY' / 'WET', or {from lap: 'DRY' / 'WET'}; None holds track_now(). No weather
    forecast is assumed. Cars on the wrong tyres for the track stop when the time they'd lose to the flag beats the
    pit loss; slicks on a wet track run at inter pace + slick_wet_s.
    neutral_now: ('SC' / 'VSC' / 'RED', laps it has left) if one is out now. events: {lap: 'SC' / 'VSC' / 'RED'}
    forced on those laps (what-ifs); random ones keep coming at the calibrated rates too.
    SC: nobody passes, stops cost the SC pit loss, and by the end of each SC lap the field has closed up. VSC: gaps freeze, stops cost the VSC pit loss.
    Red flag: the field bunches, every car gets new tyres for free. After an SC or red flag, DRS / Overtake Mode is
    off for restart_laps laps (passing odds 'pass_restart').
    Returns {'drv': [...], 'code': [...], 'pos': (runs, D) finishing positions}."""
    m, total, rng = model['m'], model['total'], np.random.default_rng(seed)
    ds = [d for d in drivers if not d['out']]
    D, R, cmps = len(ds), runs, list(m['prior'])
    loss = pit_loss or {'normal': m['pit_loss'], 'sc': m['pit_loss'] * 0.6}
    lap_s = lap_s or np.median([predict(model, d['drv'], lap, 'MEDIUM', 0) for d in ds])
    ci = lambda c: cmps.index(c) if c in cmps else cmps.index('MEDIUM')
    base = np.array([_base(model, d['drv']) for d in ds]) + rng.normal(0, m.get('pace_sd', 0.0), (R, D))  # (R, D)
    off = np.array([model['off'].get(c, 0) for c in cmps])
    deg = np.maximum([model['deg'].get(c, 0.05) for c in cmps], 0)  # tyres never get faster with age
    life = np.array([m['life'].get(c, 30) for c in cmps])
    sig = np.array([model['sigma'].get(d['drv'], 0.5) for d in ds])

    g = gaps(ds, lap_s)
    T = np.tile([g[d['drv']] for d in ds], (R, 1))
    cmp = np.tile([ci(d['cmp']) for d in ds], (R, 1))
    age = np.tile([float(d['age']) for d in ds], (R, 1))
    used = np.zeros((R, D, len(cmps)), bool)
    for j, d in enumerate(ds):
        used[:, j, [ci(c) for c in d['used'] if c]] = True
        used[:, j, ci(d['cmp'])] = True
    dry, wets, inter = [ci(c) for c in ('SOFT', 'MEDIUM', 'HARD')], [ci('INTERMEDIATE'), ci('WET')], ci('INTERMEDIATE')
    now = track_now(ds)
    changes = sorted(conditions.items()) if isinstance(conditions, dict) else [(0, conditions or now)]
    pj = next((j for j, d in enumerate(ds) if plan and d['drv'] == plan[0]), None)
    sc_left, vsc_left, ep_lap = np.zeros(R, int), np.zeros(R, int), np.zeros(R, int)
    restart_left, pitted_ep = np.zeros(R, int), np.zeros((R, D), bool)
    sc_rate = -np.log(1 - m['sc_prob']) if 'sc_prob' in m else m['sc_per_race']  # older configs: chance of >= 1 SC
    red_h, sc_h, vsc_h = (m['red_per_race'] / max(total, 1), sc_rate / max(total, 1), m['vsc_per_race'] / max(total, 1))
    pw, pwr, pn = m.get('pass'), m.get('pass_restart') or m.get('pass'), m.get('pit_neutral')
    pn_sd, herd = m.get('pit_neutral_sd') or {}, np.zeros(R)  # herd: this SC/VSC's shared pull towards stopping
    life0 = np.array([DEFAULTS['life'].get(c, 30) for c in cmps])
    forced = dict(events or {})
    if neutral_now and neutral_now[0] in NEUTRAL:
        forced.setdefault(lap + 1, neutral_now[0])
    now_left = neutral_now[1] if neutral_now else None

    def bunch(rows):  # end of an SC lap: everyone has caught the queue, at most ~0.8 s apart (cars that pitted rejoin
        # at the back of their gap first: a stop as the SC comes out is the 'free' one)
        order = np.argsort(T[rows], axis=1)
        t = np.take_along_axis(T[rows], order, 1)
        t = t[:, :1] + np.concatenate([np.zeros((len(t), 1)), np.cumsum(np.minimum(np.diff(t, axis=1), 0.8), 1)], 1)
        sub = T[rows]
        np.put_along_axis(sub, order, t, 1)
        T[rows] = sub

    for L in range(lap + 1, total + 1):
        left = total - L
        free, draw = (sc_left == 0) & (vsc_left == 0), rng.random(R)
        new_red = free & (draw < red_h) & (left > 3)
        new_sc = free & ~new_red & (draw < red_h + sc_h) & (left > 3)
        new_vsc = free & ~new_red & ~new_sc & (draw < red_h + sc_h + vsc_h) & (left > 1)
        if forced.get(L) in NEUTRAL and left >= 1:  # what-if (or out right now): every run gets it this lap
            k = forced[L]
            new_sc, new_vsc, new_red = (np.full(R, k == x) for x in NEUTRAL)
            sc_left[:], vsc_left[:] = 0, 0
        sc_left[new_sc] = max(1, now_left) if L == lap + 1 and now_left and neutral_now[0] == 'SC' else m['sc_laps']
        sc_left[new_red] = 1  # restart (behind the SC or standing) the lap after the stoppage
        vsc_left[new_vsc] = max(1, now_left) if L == lap + 1 and now_left and neutral_now[0] == 'VSC' else m['vsc_laps']
        ep_lap = np.where(new_sc | new_vsc | new_red, 0, ep_lap + 1)
        herd = np.where(new_sc, rng.normal(0, pn_sd.get('sc', 0.0), R), np.where(new_vsc, rng.normal(0, pn_sd.get('vsc', 0.0), R), herd))
        pitted_ep[new_sc | new_vsc | new_red] = False
        sc, vsc = sc_left > 0, vsc_left > 0
        wet_track = ([c for k, c in changes if k <= L] or [now])[-1] == 'WET'
        on_wet = np.isin(cmp, wets)
        wrong = on_wet != wet_track
        gain = (m['slick_wet_s'] + deg[cmp] * age if wet_track  # per lap, from switching to the right tyres
                else off[cmp] + deg[cmp] * age - off[dry].min())

        # Grands Prix: two different dry compounds unless inters/wets were used; sprints: free choice
        two = (not m['two_compounds']) | (used[:, :, dry].sum(2) >= 2) | used[:, :, wets].any(2)
        want = (age >= life[cmp]) & (left > 3) | (~two & (left <= 4))
        neutral = (sc | vsc)[:, None] & ~new_red[:, None]
        if pn:  # who takes a cheap stop under the SC / VSC: odds fitted on that era's races
            x = pit_neutral_features(age / life0[cmp], left / max(total, 1), ~two, ep_lap[:, None], vsc[:, None])
            z = sum(w * np.asarray(f, float) for w, f in zip(pn, x)) + herd[:, None]
            take = rng.random((R, D)) < 1 / (1 + np.exp(-np.clip(z, -30, 30)))
        else:
            take = (age > 0.4 * life[cmp]) & (left > 8)
        want |= neutral & ~pitted_ep & take & (left > 1)
        weather = wrong & ((left + 1) * gain > loss['normal'])
        want |= weather
        want &= ~new_red[:, None]  # red flag: tyres change for free below, nobody pays a stop
        if pj is not None:  # the planned stop is that car's only one, bar a later stop for the right tyres
            want[:, pj] = (L == plan[1]) | ((L > plan[1]) & weather[:, pj])
        # each car takes the dry compound that's cheapest to the flag (an extra stop if it won't last), and a new one
        # while it still owes the two-compound rule. ponytail: no set inventory (new vs used sets per compound)
        n = left + 1
        cost = n * off[dry] + deg[dry] * n * (n - 1) / 2 + (n > life[dry]) * loss['normal']
        cost = np.where(~two[:, :, None] & used[:, :, dry], np.inf, cost)
        new_c = np.full((R, D), inter) if wet_track else np.array(dry)[np.argmin(cost, axis=2)]
        if pj is not None and L == plan[1]:
            new_c[:, pj] = ci(plan[2])
        cmp = np.where(want, new_c, cmp)
        age = np.where(want, 0, age + 1)
        used[want, cmp[want]] = True
        pitted_ep |= want & (sc | vsc)[:, None]
        if new_red.any():  # red flag: everyone may change tyres for free (and it counts for the two-compound rule)
            fresh = new_red[:, None] & ~want
            cmp = np.where(fresh, new_c, cmp)
            age = np.where(fresh, 0, age)
            used[fresh, cmp[fresh]] = True

        pace = base + off[cmp] + deg[cmp] * age + model['trend'] * L
        if wet_track:
            pace = np.where(np.isin(cmp, wets), pace, base + off[inter] + deg[cmp] * age + model['trend'] * L + m['slick_wet_s'])
        lt = pace + rng.standard_normal((R, D)) * sig
        lt = np.where(sc[:, None], lap_s * m['sc_lap_factor'], np.where(vsc[:, None], lap_s * m['vsc_lap_factor'], lt))
        lt += want * np.where(sc, loss.get('sc', loss['normal']),
                              np.where(vsc, loss.get('vsc', loss['normal']), loss['normal']))[:, None]

        order = np.argsort(T, axis=1)  # ponytail: one pass over adjacent pairs, no multi-car battles
        t0, nt = np.take_along_axis(T, order, 1), np.take_along_axis(T + lt, order, 1)
        lts, pit = np.take_along_axis(lt, order, 1), np.take_along_axis(want, order, 1)
        pc, calm = np.take_along_axis(pace, order, 1), ~(sc | vsc)
        for i in range(1, D):
            racing = ~pit[:, i] & ~pit[:, i - 1]
            if pw:  # within 1 s at the line (Overtake Mode / DRS range): gets past with the calibrated probability
                g = t0[:, i] - t0[:, i - 1]
                z = np.where(restart_left > 0, pwr[0] + pwr[1] * (pc[:, i - 1] - pc[:, i]) + pwr[2] * g,
                             pw[0] + pw[1] * (pc[:, i - 1] - pc[:, i]) + pw[2] * g)
                p = 1 / (1 + np.exp(-np.clip(z, -30, 30)))
                passed = racing & calm & (g <= 1.0) & (rng.random(R) < p)
                nt[:, i] = np.where(passed, np.minimum(nt[:, i], nt[:, i - 1] - m['min_gap']), nt[:, i])
                held = racing & ~passed & (nt[:, i] < nt[:, i - 1] + m['min_gap'])
            else:  # can't pass unless much faster
                held = (nt[:, i] < nt[:, i - 1] + m['min_gap']) & racing & \
                       (sc | (lts[:, i - 1] - lts[:, i] < m['pass_threshold']))
            nt[:, i] = np.where(held, nt[:, i - 1] + m['min_gap'], nt[:, i])
        np.put_along_axis(T, order, nt, 1)
        if sc.any():
            bunch(sc)
        restart_left = np.where(sc_left == 1, m['restart_laps'], np.maximum(restart_left - 1, 0))  # SC in this lap
        sc_left, vsc_left = np.maximum(sc_left - 1, 0), np.maximum(vsc_left - 1, 0)

    return {'drv': [d['drv'] for d in ds], 'code': [d['code'] for d in ds],
            'pos': np.argsort(np.argsort(T, axis=1), axis=1) + 1}


def rejoin(drivers, drv, loss, lap_s=None):
    """Where drv comes out if they pit now: (position, car ahead, gap ahead, car behind, gap behind).
    lap_s: lap_time(), for lapped cars whose interval is in laps (see gaps())."""
    ds = [d for d in drivers if not d['out']]
    sec = gaps(ds, lap_s)
    g = {d['drv']: (sec[d['drv']], d['code']) for d in ds}
    me = g[drv][0] + loss
    others = sorted((v, c) for k, (v, c) in g.items() if k != drv)
    ahead = [o for o in others if o[0] <= me]
    behind = [o for o in others if o[0] > me]
    return (len(ahead) + 1, ahead[-1][1] if ahead else None, me - ahead[-1][0] if ahead else None,
            behind[0][1] if behind else None, behind[0][0] - me if behind else None)


def stops_owed(d, laps_left, m=MODEL):
    """Stops a car still has to make, roughly: as many as its tyres need to reach the flag (default lives), and at
    least one while it owes the two-compound rule. Wet tyres: 0 (the weather decides)."""
    dry = ('SOFT', 'MEDIUM', 'HARD')
    if laps_left <= 0 or d['cmp'] not in dry:
        return 0
    life = m['life']
    left_on_set = life.get(d['cmp'], 30) - (d['age'] or 0)
    need = 0 if laps_left <= left_on_set else int(np.ceil((laps_left - left_on_set) / max(life.get(c, 30) for c in dry)))
    used = set(d.get('used') or ()) | {d['cmp']}
    owes = m.get('two_compounds', True) and len(used & set(dry)) < 2 and not used & {'INTERMEDIATE', 'WET'}
    return max(need, int(owes))


def battle(ds, laps, me, other, lap, total, loss, m=MODEL, lap_s=None):
    """One battle from me's side: other's gap on track (s, + = other behind), gap net of the stops each still has
    to make, pace edge (s/lap over their last 5 common green laps, + = me faster), laps until me catches other (None if
    not closing or other is behind), and the gap at the end of each of the last 10 laps (oldest first)."""
    g = gaps(ds, lap_s)
    by = {d['drv']: d for d in ds}
    a, b = by[me], by[other]
    gap = g[other] - g[me]
    left = max(total - lap, 0)
    owed_a, owed_b = stops_owed(a, left, m), stops_owed(b, left, m)
    green = lambda drv: {l['lap']: l['t'] for l in laps if l['drv'] == drv and l['t'] and not l['pit'] and not l['sc']
                         and l['lap'] > 1}
    ta, tb = green(me), green(other)
    common = sorted(ta.keys() & tb.keys())[-5:]
    edge = float(np.mean([tb[n] - ta[n] for n in common])) if len(common) >= 2 else None
    catch = int(np.ceil(-gap / edge)) if edge and edge > 0 and gap < 0 else None
    end = {(l['drv'], l['lap']): l['gap'] for l in laps}
    trend = [end[(other, n)] - end[(me, n)] for n in range(max(lap - 9, 1), lap + 1)
             if end.get((other, n)) is not None and end.get((me, n)) is not None]
    return {'gap': gap, 'net': gap + (owed_b - owed_a) * loss, 'owed': (owed_a, owed_b), 'edge': edge,
            'catch': catch if catch is None or catch <= left else None, 'trend': trend,
            'close': abs(gap) < 1.0 and abs(a['pos'] - b['pos']) == 1}


def real_rival(ds, laps, me, lap, total, loss, m=MODEL, lap_s=None):
    """The car nearest me once both have made their remaining stops: (racing number, net gap), or None."""
    out = [(abs(x['net']), d['drv'], x['net']) for d in ds if d['drv'] != me and not d['out']
           for x in [battle(ds, laps, me, d['drv'], lap, total, loss, m, lap_s)]]
    return min(out)[1:] if out else None


def what_if(drivers, lap, model, drv, pit_lap, compound, runs=400, pit_loss=None, lap_s=None, conditions=None,
            neutral_now=None, events=None):
    """Same random numbers for both runs, so the difference is the strategy, not the dice."""
    a = simulate(drivers, lap, model, None, runs, 1, pit_loss, lap_s, conditions, neutral_now, events)
    b = simulate(drivers, lap, model, (drv, pit_lap, compound), runs, 1, pit_loss, lap_s, conditions, neutral_now, events)
    j = a['drv'].index(drv)
    return {'base': a['pos'][:, j], 'plan': b['pos'][:, j]}


def best_window(drivers, lap, model, drv, runs=150, pit_loss=None, compounds=('MEDIUM', 'HARD', 'SOFT'), lap_s=None,
                conditions=None, neutral_now=None, events=None):
    """Mean finishing position for every (pit lap, compound) in the next 25 laps; best first."""
    out = []
    for c in compounds:
        for L in range(lap + 1, min(lap + 26, model['total'] - 1)):
            r = simulate(drivers, lap, model, (drv, L, c), runs, 1, pit_loss, lap_s, conditions, neutral_now, events)
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
                         'gap': f"+{last['gap']}" if last['gap'] is not None else ('' if last['pos'] == 1 else '1L'),
                         'int': f"+{last['int']}" if last['int'] is not None else '',
                         'used': {l['cmp'] for l in mine if l['lap'] > 1}})
    return sorted(rows, key=lambda d: d['pos'])


def backtest(path, fit_to=20):
    from .live import laps_from_archive
    st, laps = laps_from_archive(path)
    total = st.session()['total']
    loss = st.pit_loss()
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
            pred = rejoin(snapshot(laps, l['lap'] - 1), l['drv'], loss['normal'],
                          lap_time([o for o in laps if o['lap'] < l['lap']]))[0]
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
