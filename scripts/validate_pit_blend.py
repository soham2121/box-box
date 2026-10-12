"""Pit loss during a race: MultiViewer's published value for the circuit, blended with the stops measured so far.
For every green-flag stop of the 2025 and 2026 races and sprints, the estimate as it stood just before that stop
(only stops whose out-lap was done) predicts where the car rejoins ("pit now"); scored against where it really came
out after its out-lap, and against the race's own median loss (all its stops). w = how many stops the published value
counts as: 'api' never moves off it (the old behaviour), 0 = the race's own stops only (from the third).

Run: .venv\\Scripts\\python scripts\\validate_pit_blend.py   (races come from the disk cache)
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding='utf-8')
import fastf1
import numpy as np
fastf1.set_log_level('ERROR')
from lightsout import calibrate as c, live, sim

WEIGHTS = ('api', 0, 2, 4, 6, 10)


def estimate(api, samples, w):
    if api is None or w == 'api':
        return api if api is not None else (float(np.median(samples)) if len(samples) >= 3 else None)
    if w == 0:
        return float(np.median(samples)) if len(samples) >= 3 else api
    return (w * api + len(samples) * float(np.median(samples))) / (w + len(samples)) if samples else api


def race_rows(path):
    st, laps = live.laps_from_archive(path)
    api = (st.pit_known or {}).get('normal')
    stops = st.stops()
    final = sim.pit_loss_samples(laps, stops)
    if api is None or len(final) < 4:
        return None
    truth = float(np.median(final))
    by = {(l['drv'], l['lap']): l for l in laps}
    rows = []
    for s in sorted(stops, key=lambda s: s['lap']):
        n = s['lap']
        prev, nxt, cur = by.get((s['drv'], n - 1)), by.get((s['drv'], n + 1)), by.get((s['drv'], n))
        if not (cur and prev and nxt) or n <= 2 or cur['sc'] or prev['sc'] or nxt['sc'] or prev['pit']:
            continue
        done = [x for x in stops if x['lap'] + 1 < n]  # stops whose out-lap is complete before this in-lap starts
        before = [l for l in laps if l['lap'] < n]
        samples = sim.pit_loss_samples(before, done)
        snap = sim.snapshot(laps, n - 1)
        if not any(d['drv'] == s['drv'] for d in snap):
            continue
        row = {'truth': truth, 'published': api, 'n': len(samples)}
        for w in WEIGHTS:
            e = estimate(api, samples, w)
            row[w] = (abs(e - truth), abs(sim.rejoin(snap, s['drv'], e, sim.lap_time(before))[0] - nxt['pos']))
        rows.append(row)
    return rows


def main():
    out = {}
    for year in (2025, 2026):
        for label, p in c.races([year]):
            try:
                rows = race_rows(p)
            except Exception as e:
                print(f'skip {label}: {e}')
                continue
            if rows:
                out[label] = rows
                r0 = rows[0]
                print(f"{label:<40} published {r0['published']:5.1f}  race median {r0['truth']:5.1f}  stops {len(rows)}")
    allrows = [r for rows in out.values() for r in rows]
    print(f'\n{len(out)} races, {len(allrows)} green stops')
    for w in WEIGHTS:
        loss_err = np.mean([r[w][0] for r in allrows])
        place_err = np.mean([r[w][1] for r in allrows])
        exact = np.mean([r[w][1] == 0 for r in allrows])
        print(f'w={str(w):>3}: pit loss off by {loss_err:4.2f} s, rejoin off by {place_err:4.2f} places, exact {exact:4.0%}')


if __name__ == '__main__':
    main()
