import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def make_race(drivers=10, laps=30, base=90.0, stops=(), slow=None, sc=(), lap1_pit=True, loss=22.0, inlap_share=3.0):
    """Synthetic State.laps: driver i is 0.1 s/lap slower than driver i-1, fuel burn 0.02 s/lap.
    stops: [(drv, in_lap)] lose `loss` s split over in-lap and out-lap. slow: {lap: factor} without an SC flag."""
    slow, out = slow or {}, []
    stop_laps = {(d, n) for d, n in stops} | {(d, n + 1) for d, n in stops}
    for i in range(drivers):
        d = str(i + 1)
        for n in range(1, laps + 1):
            t = base + 0.1 * i - 0.02 * n
            t *= slow.get(n, 1.0)
            if (d, n) in {(x, y) for x, y in stops}:
                t += inlap_share
            if (d, n - 1) in {(x, y) for x, y in stops}:
                t += loss - inlap_share
            out.append({'drv': d, 'lap': n, 't': None if n == 1 else t, 'cmp': 'MEDIUM', 'age': n, 'pos': i + 1,
                        'pit': (d, n) in stop_laps or (lap1_pit and n == 1), 'sc': n in sc, 'gap': 0.5 * i, 'int': 0.5,
                        'at': 0})
    return out
