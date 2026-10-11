"""Live timing: SignalR feed (F1 TV or no auth), archive replay, state merge, lap log, estimated car positions."""
import base64
import collections
import copy
import json
import queue
import re
import socket
import threading
import time
import webbrowser
import zlib
from http.server import HTTPServer

import numpy as np

from . import data


RESET = '__reset__'  # pseudo-topic: a replay jumped backwards, start the session state again
NEUTRALISED = {'4', '5', '6', '7'}  # TrackStatus: SC, red flag, VSC, VSC ending ('2' is just a local yellow)
# Position.z also carries the safety car as 241, 242 or 243 (which one varies by race; 2025-26 archive: each only moves
# while TrackStatus is SC). Parked in the pit lane otherwise, so it's shown only while it moves.
SAFETY_CARS = {'241', '242', '243'}
# GPS comes about once a second, 4-5 samples per message, and the samples' timestamps don't match their positions
# closely (0.24 s for 2 m, then 0.34 s for 16 m), so a straight line between samples jerks. Cars glide along a curve
# fitted to the samples within GLIDE_W s either side of the moment drawn (Bahrain 2026: within 7 m of the raw path),
# when that window is already in hand: live with a delay of at least SMOOTH_DELAY s, or a replay. The moment drawn is
# GLIDE_BACK s (+ the 0.5 s between pumps, in feed time) behind the newest data: the window, the longest gap between
# messages (1.3 s) and some margin. Live with a smaller delay than that, the map runs behind the timing by the rest.
SMOOTH_DELAY = 2
GLIDE_W = 1.25
GLIDE_BACK = 2.75


def merge(base, upd):
    """Apply an F1 live timing delta: dicts merge recursively, {"3": ...} patches list index 3.
    New parts are copied: later deltas edit the merged state in place, and must not edit the message they came from
    (a replay plays its messages again after jumping back)."""
    if not isinstance(upd, dict) or not isinstance(base, (dict, list)):
        return copy.deepcopy(upd)
    for k, v in upd.items():
        if k == '_deleted':
            continue
        if isinstance(base, list):
            k = int(k)
            base.extend([None] * (k + 1 - len(base)))
        cur = base[k] if isinstance(base, list) else base.get(k)
        base[k] = merge(cur, v)
    return base


def seq(x):
    """Live timing lists arrive either as lists or as {"0": ..., "1": ...}."""
    return [x[k] for k in sorted(x, key=int)] if isinstance(x, dict) else (x or [])


def secs(s):
    """'1:49.315' / '+2.020' / '25.282' -> float; '', 'LAP 3', '1 L' -> None."""
    try:
        m, _, sec = str(s).lstrip('+').rpartition(':')
        return float(sec) + 60 * float(m or 0)
    except ValueError:
        return None


def unz(s):
    return json.loads(zlib.decompress(base64.b64decode(s), -zlib.MAX_WBITS))


def clock(sample):
    """Seconds into the day of a Position sample's Timestamp ('2026-10-04T06:26:25.1743756Z'), None without one."""
    try:
        h, m, sec = sample['Timestamp'][11:].rstrip('Z').split(':')
        return int(h) * 3600 + int(m) * 60 + float(sec)
    except (KeyError, TypeError, ValueError):
        return None


class Track:
    def __init__(self, info):
        xy = np.c_[info['x'], info['y']].astype(float)
        self.xy = np.vstack([xy, xy[:1]])  # close the loop
        self.s = np.r_[0, np.cumsum(np.hypot(*np.diff(self.xy, axis=0).T))]
        self.ms = info.get('miniSectorsIndexes') or []
        self.rotation = info.get('rotation', 0)
        self.pit_loss = {k: float(v) for k, v in (info.get('pitLoss') or {}).items()}
        self.learned = None  # {'sectors', 'profile'} from GPS at this circuit (learn_positions), for estimated positions
        self.landmarks(info)

    def landmarks(self, info):
        """Corners [(number, x, y, label angle in degrees)] and marshal sectors {number: (outline index where it
        starts, where the next one starts)} from a circuit API payload (another year's works too: same Position.z
        frame). The last sector ends where the first starts, so its end index is the smaller one."""
        self.corners = [(c['number'], c['trackPosition']['x'], c['trackPosition']['y'], c.get('angle', 0))
                        for c in info.get('corners') or []]
        ms = sorted(info.get('marshalSectors') or [], key=lambda m: m['number'])
        idx = self.snap([(m['trackPosition']['x'], m['trackPosition']['y']) for m in ms])
        self.marshal = {m['number']: (i, idx[(k + 1) % len(idx)]) for k, (m, i) in enumerate(zip(ms, idx))}

    def snap(self, pts):
        """Outline index nearest each point, in lap order: each searches only the half lap after the one before, so a
        point where the track crosses itself (Suzuka) lands on the right branch."""
        n, out = len(self.xy) - 1, []
        for x, y in pts:
            d = np.hypot(self.xy[:-1, 0] - x, self.xy[:-1, 1] - y)
            if out:
                d = np.where((np.arange(n) - out[-1]) % n < n // 2, d, np.inf)
            out.append(int(np.argmin(d)))
        return out

    def at(self, f):
        d = (f % 1) * self.s[-1]
        return np.interp(d, self.s, self.xy[:, 0]), np.interp(d, self.s, self.xy[:, 1])

    def bounds(self, counts):
        """Lap fraction at the end of each timing mini-sector (counts: mini-sectors per sector). Learned from GPS at this
        circuit, per sector: a session can have another mini-sector count in one sector (Baku 2026: 8/10/8 in
        qualifying, 8/10/9 in the race), which then spreads evenly between that sector's learned ends. Else the circuit
        API's mini-sector indexes, else equal lengths."""
        n = sum(counts) or 1
        secs = (self.learned or {}).get('sectors')
        if secs and len(secs) == len(counts):
            out, start = [], 0.0
            for lb, c in zip(secs, counts):
                out += lb if len(lb) == c else list(start + (lb[-1] - start) * np.arange(1, c + 1) / c)
                start = lb[-1]
            return np.array(out)
        if len(self.ms) == n:
            return self.s[self.ms] / self.s[-1]
        return np.arange(1, n + 1) / n  # ponytail: assumes equal mini-sector lengths when nothing better is known

    def advance(self, f0, dt):
        """Lap fraction after `dt` (share of a lap time) from f0. Uses the circuit's time->distance profile (slow corners
        take more of the lap than their length) when learned, else constant speed."""
        prof = (self.learned or {}).get('profile')
        if not prof:
            return f0 + dt
        tau = np.linspace(0, 1, len(prof))
        return float(np.interp(np.interp(f0, prof, tau) + dt, tau, prof, right=1.0))


class State:
    """Everything the widgets read. Feeds push into .q from their threads; pump() applies on the UI thread."""

    def __init__(self):
        self.q = queue.Queue()
        self.d = {}            # topic -> merged data
        self.laps = []         # completed laps, see _lap()
        self.gps = {}          # racing number -> (x, y), only when Position.z flows (F1 TV / replay)
        self.gps_t = 0
        self.trail = {}        # racing number -> deque of (feed time, x, y): GPS samples, ahead of .gps while held back
        self.gps_off = None    # feed time minus GPS sample clock, for the trail (add_trail)
        self.sc_moved = {}     # safety car number -> feed time it last moved
        self.clock_t = 0       # feed time the last ExtrapolatedClock message arrived
        self.delay = 0         # s to hold live data back, to match a video stream that runs behind live timing
        self.held = collections.deque()
        self.prog = {}         # racing number -> [lap fraction at snap, snap time, cap fraction]
        self.flags = {}        # racing number -> {'pit', 'sc'} seen during the current lap
        self.lastlap = {}
        self.track = None
        self.feed = None
        self.t = 0             # feed clock of the last message
        self.pit_known = None  # pit loss from the circuit API {'normal', 'sc', 'vsc'}
        self.pit_weekend = None  # pit loss measured in an earlier race of this weekend (sprint)
        self.pit_typical = None  # median pit loss over the circuit catalogue
        self.neutral = None    # [SC / VSC / RED, lap it began, ending] while one is out (the sim starts from it)
        self._pit = (None, None)
        self._yellow = (None, {})  # (message count, yellow_sectors())

    def pump(self, budget=0.4):
        """Apply queued messages for up to `budget` s (a replay jump queues tens of thousands at once).
        Live with a delay: messages wait in .held until they're `delay` s old."""
        end = time.monotonic() + budget
        while True:
            try:
                m = self.q.get_nowait()
            except queue.Empty:
                break
            if m[0] == RESET:
                self.trail = {}
            elif m[0] in ('Position', 'Position.z'):  # unpacked once, here, so the trail runs ahead of the delay
                m = ('Position', unz(m[1]) if m[0].endswith('.z') else m[1], m[2])
                self.add_trail(m[1], m[2])
            self.held.append(m)
        now = self.now()
        while self.held and time.monotonic() < end:
            if self.lag() and self.held[0][2] > now:
                return
            self.apply(*self.held.popleft())

    def lag(self):
        """The delay applies to the live feed only (replays have their own clock)."""
        return self.delay if isinstance(self.feed, LiveFeed) else 0

    def reset(self):
        """Back to an empty session (a replay jumping backwards); keeps the queue, feed, track and pit-loss sources."""
        keep = {k: getattr(self, k) for k in ('q', 'held', 'delay', 'feed', 'track', 'pit_known', 'pit_weekend', 'pit_typical',
                                              'trail', 'gps_off')}  # trail: cleared by pump() when it queues the reset
        self.__init__()
        self.__dict__.update(keep)

    def now(self):
        return self.feed.now() - self.lag() if self.feed else self.t

    def glide_at(self):
        """Feed time the map draws GPS cars at, between fixes; None: each car jumps to its latest fix."""
        if not self.feed or isinstance(self.feed, LiveFeed) and self.lag() < SMOOTH_DELAY:
            return None
        return self.feed.now() - max(self.lag(), self.glide_back())

    def glide_back(self):
        return GLIDE_BACK + 0.6 * max(getattr(self.feed, 'speed', 1), 1)

    def add_trail(self, x, t):
        """GPS samples of one Position message into .trail. The message time t is its last sample's; the others sit
        before it as far apart as their own timestamps."""
        samples = x.get('Position') or []
        end = clock(samples[-1]) if samples else None
        if end is not None:  # t is when the message arrived: network jitter moved whole messages back and forth (cars
            # rubber-banded at any delay). Go by the samples' own clock, offset by the quickest delivery seen so far.
            if self.gps_off is None:
                self.gps_off = t - end
            late = (t - end - self.gps_off + 43200) % 86400 - 43200  # % : past midnight
            self.gps_off += min(late, 0)
            t -= max(late, 0)
        for s in samples:
            at = t - (end - clock(s)) % 86400 if end is not None and clock(s) is not None else t  # % : past midnight
            for n, e in s['Entries'].items():
                if e.get('X') or e.get('Y'):
                    self.trail.setdefault(n, collections.deque()).append((at, e['X'], e['Y']))
        old = self.now() - self.glide_back() - GLIDE_W - 2  # keeps memory bounded when nothing draws them (no smoothing)
        for tr in self.trail.values():
            while len(tr) > 1 and tr[1][0] <= old:
                tr.popleft()

    def glide(self, n, at):
        """(x, y) of car n at feed time `at`: a quadratic in time fitted to its samples within GLIDE_W s either side,
        the nearer ones weighing more (tricube, so a sample fades in and out of the window without a jump)."""
        tr = self.trail.get(n)
        if not tr:
            return None
        while len(tr) > 1 and tr[1][0] <= at - GLIDE_W:
            tr.popleft()
        win = []
        for smp in tr:  # the trail runs far ahead with a long delay: only walk the window
            if smp[0] >= at + GLIDE_W:
                break
            win.append(smp)
        # no sample on one side (seek, signal lost, not here yet): hold the nearest
        if not win or win[0][0] > at:
            return tr[0][1:]
        if win[-1][0] <= at:
            return win[-1][1:]
        S = np.array(win, dtype=float)
        d = (S[:, 0] - at) / GLIDE_W
        w = np.sqrt(np.clip(1 - np.abs(d) ** 3, 0, 1) ** 3)
        if (w > 0).sum() < 4:  # too few for a curve: straight line between the two either side
            i = int(np.searchsorted(S[:, 0], at, side='right'))
            k = (at - S[i - 1, 0]) / (S[i, 0] - S[i - 1, 0])
            return tuple(S[i - 1, 1:] + k * (S[i, 1:] - S[i - 1, 1:]))
        c = np.linalg.lstsq(np.c_[np.ones_like(d), d, d * d] * w[:, None], S[:, 1:] * w[:, None], rcond=None)[0]
        return float(c[0, 0]), float(c[0, 1])

    def apply(self, topic, x, t):
        if topic == RESET:
            return self.reset()
        self.t = t
        if topic.endswith('.z'):
            topic, x = topic[:-2], unz(x)
        if topic == 'Position':
            if x.get('Position'):
                gps = {n: (e['X'], e['Y']) for n, e in x['Position'][-1]['Entries'].items() if e.get('X') or e.get('Y')}
                for n in SAFETY_CARS & gps.keys():
                    if n in self.gps and self.gps[n] != gps[n]:
                        self.sc_moved[n] = t
                self.gps, self.gps_t = gps, t
            return
        if topic == 'CarData':
            if x.get('Entries'):
                self.d['CarData'] = x['Entries'][-1]['Cars']
            return
        if topic == 'TrackStatus' and x.get('Status') in NEUTRALISED:
            for f in self.flags.values():
                f['sc'] = True
        if topic == 'TrackStatus' and 'Status' in x:
            kind = {'4': 'SC', '5': 'RED', '6': 'VSC', '7': 'VSC'}.get(str(x['Status']))
            if not kind:
                self.neutral = None
            elif not self.neutral or self.neutral[0] != kind:
                self.neutral = [kind, self.session()['lap'], False]
            if self.neutral and str(x['Status']) == '7':  # VSC ending: green within seconds
                self.neutral[2] = True
        if topic == 'RaceControlMessages' and self.neutral and self.neutral[0] == 'SC':
            msgs = x.get('Messages') if isinstance(x, dict) else None
            if any((mm or {}).get('Status') == 'IN THIS LAP' for mm in (msgs.values() if isinstance(msgs, dict) else msgs or [])):
                self.neutral[2] = True
        if topic == 'TimingData':
            return self._timing(x, t)
        if topic == 'ExtrapolatedClock':
            self.clock_t = t
        self.d[topic] = merge(self.d.get(topic), x)

    def _timing(self, x, t):
        lines = x.get('Lines', {}) if isinstance(x, dict) else {}
        passed = {}
        for n, ln in lines.items():
            for s, sec in (ln.get('Sectors') or {}).items() if isinstance(ln.get('Sectors'), dict) else ():
                segs = sec.get('Segments') if isinstance(sec, dict) else None
                for k, v in (segs.items() if isinstance(segs, dict) else ()):
                    if v.get('Status'):
                        passed[n] = (int(s), int(k))
        self.d['TimingData'] = merge(self.d.get('TimingData'), x)
        all_lines = self.d['TimingData'].get('Lines', {})
        for n, ln in lines.items():
            line = all_lines[n]
            f = self.flags.setdefault(n, {'pit': False, 'sc': False})
            started = self.d.get('SessionStatus', {}).get('Status') not in (None, 'Inactive')  # pre-race: pit lane -> grid
            if started and (ln.get('InPit') or ln.get('PitOut')):
                f['pit'] = True
            if 'NumberOfLaps' in ln:
                self._lap(n, line)
                self.prog[n] = [0.0, t, self._cap(n, line, 0)]
            elif n in passed and self.track:
                counts = [len(seq(s.get('Segments'))) for s in seq(line.get('Sectors'))]
                s, k = passed[n]
                g = sum(counts[:s]) + k
                b = self.track.bounds(counts)
                if g < len(b):
                    self.prog[n] = [b[g], t, b[g + 1] if g + 1 < len(b) else 1.0]

    def _cap(self, n, line, g):
        if not self.track:
            return 1.0
        b = self.track.bounds([len(seq(s.get('Segments'))) for s in seq(line.get('Sectors'))])
        return b[g]

    def _lap(self, n, line):
        stint = (seq(self.d.get('TimingAppData', {}).get('Lines', {}).get(n, {}).get('Stints')) or [{}])[-1] or {}
        f = self.flags[n]
        if self.lastlap.get(n) == line['NumberOfLaps']:  # count re-sent unchanged (e.g. on reconnect)
            return
        self.lastlap[n] = line['NumberOfLaps']
        self.laps.append({
            'drv': n, 'lap': line['NumberOfLaps'], 't': secs((line.get('LastLapTime') or {}).get('Value')),
            'cmp': stint.get('Compound', 'UNKNOWN'), 'age': stint.get('TotalLaps', 0),
            'pit': f['pit'], 'sc': f['sc'], 'pos': int(line.get('Position') or 0),
            'gap': secs(line.get('GapToLeader')), 'int': secs((line.get('IntervalToPositionAhead') or {}).get('Value')),
            'at': self.t,
        })
        started = self.d.get('SessionStatus', {}).get('Status') not in (None, 'Inactive')
        self.flags[n] = {'pit': started and bool(line.get('InPit')),
                         'sc': self.d.get('TrackStatus', {}).get('Status') in NEUTRALISED}

    # ---- views for widgets -------------------------------------------------
    def drivers(self):
        """Running order with everything the tower/sim need."""
        lines = self.d.get('TimingData', {}).get('Lines', {})
        app = self.d.get('TimingAppData', {}).get('Lines', {})
        dl = self.d.get('DriverList', {})
        out = []
        for n, ln in lines.items():
            stints = [s for s in seq(app.get(n, {}).get('Stints')) if s]
            info = dl.get(n, {}) if isinstance(dl.get(n), dict) else {}
            out.append({
                'drv': n, 'code': info.get('Tla', n), 'colour': info.get('TeamColour', '888888'),
                'pos': int(ln.get('Position') or 99), 'laps': ln.get('NumberOfLaps', 0),
                'gap': ln.get('GapToLeader', ''), 'int': (ln.get('IntervalToPositionAhead') or {}).get('Value', ''),
                'last': (ln.get('LastLapTime') or {}).get('Value', ''), 'best': (ln.get('BestLapTime') or {}).get('Value', ''),
                'cmp': stints[-1].get('Compound', '?') if stints else '?', 'age': stints[-1].get('TotalLaps', 0) if stints else 0,
                'used': {s.get('Compound') for s in stints}, 'stops': ln.get('NumberOfPitStops', max(len(stints) - 1, 0)),
                'pit': bool(ln.get('InPit')), 'out': bool(ln.get('Retired') or ln.get('Stopped')),
            })
        return sorted(out, key=lambda d: d['pos'])

    def positions(self):
        """[(racing number, x, y, in_pit)] and whether they're real GPS or estimated."""
        lines = self.d.get('TimingData', {}).get('Lines', {})
        # out of the session: F1 sets Stopped when a car stops (cleared if it gets going again; Retired is rarely
        # sent) and GPS keeps sending its last spot, which would leave a dot stuck on track
        gone = lambda n: lines.get(n, {}).get('Retired') or lines.get(n, {}).get('Stopped')
        if self.gps and self.now() - self.gps_t < 10 * max(getattr(self.feed, 'speed', 1), 1):  # replays: feed time runs faster
            at = self.glide_at()
            return [(n, *((at is not None and self.glide(n, at)) or xy), bool(lines.get(n, {}).get('InPit')))
                    for n, xy in self.gps.items()
                    if not gone(n) and (n not in SAFETY_CARS or self.gps_t - self.sc_moved.get(n, -1e9) < 30)], True
        if not self.track:
            return [], False
        now, out = self.now(), []
        for n, (f0, t0, cap) in self.prog.items():
            ln = lines.get(n, {})
            if gone(n):
                continue
            lap_t = secs((ln.get('LastLapTime') or {}).get('Value')) or secs((ln.get('BestLapTime') or {}).get('Value')) or 100
            f = f0 if ln.get('InPit') else min(self.track.advance(f0, (now - t0) / lap_t), cap)
            out.append((n, *self.track.at(f), bool(ln.get('InPit'))))
        return out, False

    def stops(self):
        """Real pit stops [{'drv', 'lap' (in-lap), 'lane', 'stationary'}] from PitStopSeries, else from the lap pit flags."""
        out = []
        for n, lst in (self.d.get('PitStopSeries', {}).get('PitTimes') or {}).items():
            for x in seq(lst):
                ps = (x or {}).get('PitStop') or {}
                if ps.get('Lap'):
                    out.append({'drv': n, 'lap': int(ps['Lap']), 'lane': secs(ps.get('PitLaneTime')),
                                'stationary': secs(ps.get('PitStopTime'))})
        if out or 'PitStopSeries' in self.d:
            return out
        from .sim import stops_from_laps
        return stops_from_laps(self.laps)

    def pit_loss(self, m=None):
        """sim.pit_loss() for this session, recomputed only when laps or stops change."""
        from . import sim
        stops = self.stops()
        key = (len(self.laps), len(stops), repr((self.pit_known, self.pit_weekend, self.pit_typical, sorted((m or {}).items()))))
        if self._pit[0] != key:
            self._pit = (key, sim.pit_loss(self.laps, stops, self.pit_known, self.pit_weekend, self.pit_typical, m or sim.MODEL))
        return self._pit[1]

    def prediction(self):
        """F1's own championship projection for this race/sprint (ChampionshipPrediction topic):
        {'drivers': [...], 'teams': [...]} sorted by predicted points, each row with 'points' and 'delta'; or None.
        CurrentPoints is the table before this session, so points already awarded (e.g. the sprint) count once."""
        cp = self.d.get('ChampionshipPrediction') or {}
        if not cp.get('Drivers'):
            return None
        dl = {n: i for n, i in self.d.get('DriverList', {}).items() if isinstance(i, dict)}
        colour = {i.get('TeamName'): i.get('TeamColour', '888888') for i in dl.values()}

        def row(e, **kw):
            pts = e.get('PredictedPoints', e.get('CurrentPoints', 0)) or 0
            return {**kw, 'points': pts, 'delta': pts - (e.get('CurrentPoints') or 0)}
        drivers = [row(e, drv=n, code=dl.get(n, {}).get('Tla', n), team=dl.get(n, {}).get('TeamName', ''),
                       colour=dl.get(n, {}).get('TeamColour', '888888')) for n, e in cp['Drivers'].items() if e]
        teams = [row(e, name=e.get('TeamName') or k, colour=colour.get(e.get('TeamName'), '888888'))
                 for k, e in (cp.get('Teams') or {}).items() if e]
        return {'drivers': sorted(drivers, key=lambda r: -r['points']), 'teams': sorted(teams, key=lambda r: -r['points'])}

    def neutral_now(self, m):
        """('SC' / 'VSC' / 'RED', laps it likely has left) for the sim, or None when green. An SC 'in this lap' has
        one left, a VSC that's ending none; otherwise the era's typical length minus the laps already run."""
        if not self.neutral:
            return None
        kind, since, ending = self.neutral
        if kind == 'RED':
            return ('RED', 1)
        if ending:
            return ('SC', 1) if kind == 'SC' else None
        typical = m['sc_laps'] if kind == 'SC' else m['vsc_laps']
        return (kind, max(1, round(typical) - (self.session()['lap'] - since)))

    def yellow_sectors(self):
        """{marshal sector: 'YELLOW' / 'DOUBLE YELLOW'} waved right now, from race control: a sector's CLEAR ends it,
        a track-wide GREEN / CLEAR / CHEQUERED ends them all."""
        msgs = [m for m in seq(self.d.get('RaceControlMessages', {}).get('Messages')) if isinstance(m, dict)]
        if self._yellow[0] != len(msgs):
            out = {}
            for m in msgs:
                flag, scope = m.get('Flag'), m.get('Scope')
                if scope == 'Sector' and m.get('Sector'):
                    if flag in ('YELLOW', 'DOUBLE YELLOW'):
                        out[int(m['Sector'])] = flag
                    elif flag == 'CLEAR':
                        out.pop(int(m['Sector']), None)
                elif scope == 'Track' and flag in ('GREEN', 'CLEAR', 'CHEQUERED'):
                    out = {}
            self._yellow = (len(msgs), out)
        return self._yellow[1]

    def season(self):
        """Year of the session (SessionInfo StartDate), or None before it arrives."""
        d = str(self.d.get('SessionInfo', {}).get('StartDate', ''))[:4]
        return int(d) if d.isdigit() else None

    def clock(self):
        """Session time left in s, or None. F1 sends Remaining when the clock changes state; while Extrapolating it
        counts down from the moment the message arrived (its Utc matches the feed time, checked on archived quali)."""
        c = self.d.get('ExtrapolatedClock') or {}
        try:
            h, m, sec = (float(v) for v in c['Remaining'].split(':'))
        except (KeyError, ValueError, AttributeError):
            return None
        left = h * 3600 + m * 60 + sec
        return max(left - (self.now() - self.clock_t if c.get('Extrapolating') else 0), 0)

    def quali(self):
        """{'part': 1-3, 'label': 'Q2' / 'SQ2', 'safe': how many go through to the next part (None in the last)},
        or None outside qualifying. NoEntries = cars in each part (22, 16, 10 in 2026). Per-line 'Cutoff' is the 107 %
        rule, not the drop zone."""
        td = self.d.get('TimingData', {})
        part = td.get('SessionPart')
        if not part:
            return None
        n = seq(td.get('NoEntries'))
        return {'part': part, 'label': ('SQ' if 'Sprint' in self.d.get('SessionInfo', {}).get('Name', '') else 'Q') + str(part),
                'safe': n[part] if part < len(n) else None}

    def session(self):
        si = self.d.get('SessionInfo', {})
        lc = self.d.get('LapCount', {})
        return {'name': f"{si.get('Meeting', {}).get('Name', '')} {si.get('Name', '')}".strip(),
                'type': si.get('Type', ''), 'lap': lc.get('CurrentLap', 0), 'total': lc.get('TotalLaps', 0),
                'track': re.sub(r'(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])', ' ',  # 'SCDeployed' -> 'SC Deployed'
                                self.d.get('TrackStatus', {}).get('Message', '')), 'status': self.d.get('SessionStatus', {}).get('Status', '')}


def load_track(state, si, positions=False):
    """Outline + pit-loss sources for a SessionInfo payload (feed thread; network).
    New circuits fall back to an archived lap trace. positions=True (feeds that may run without GPS) also learns
    where this circuit's timing points are, on a background thread (see learn_positions)."""
    key, year = si['Meeting']['Circuit']['Key'], int(si['StartDate'][:4])
    try:
        state.track = Track(data.circuit(key, year))
    except Exception as e:
        print('circuit API has no outline:', e)
        meeting = si.get('Path', '').rsplit('/', 2)[0]
        done = [p for _, p in data.past_sessions(year, n=999) if meeting and p.startswith(meeting)]
        if done:
            try:
                state.track = Track(outline_from_archive(done[-1]))  # earliest finished session of this weekend
            except Exception as e:  # map stays empty; timing still works
                print('outline fallback failed:', e)
        if state.track:
            try:  # turn numbers, marshal sectors: the newest year MultiViewer has this circuit (Singapore: 2023)
                known = (data.circuit_catalog().get(str(key)) or {}).get('years') or []
                if known:
                    state.track.landmarks(data.circuit(key, max(known)))
            except Exception as e:  # map without them
                print('no turn numbers or marshal sectors:', e)
    try:
        load_pit_loss(state, si)
    except Exception as e:  # sim falls back to the model default
        print('pit loss sources failed:', e)
    if positions and state.track:
        threading.Thread(target=attach_positions, args=(state, state.track, si), daemon=True).start()


def attach_positions(state, track, si):
    try:
        src = position_source(si)
        if src:
            track.learned = learned_positions(src, track)
            print('estimated positions calibrated on', src)  # path only: no credentials involved
    except Exception as e:  # estimates fall back to equal mini-sectors
        print('position calibration failed:', e)


def position_source(si):
    """The archived session to learn timing-point positions from: the latest finished one of this weekend, else this
    circuit's latest one in the two seasons before (FP1 has nothing earlier at the same weekend)."""
    meeting, year = si.get('Path', '').rsplit('/', 2)[0], int(si['StartDate'][:4])
    done = [p for _, p in data.past_sessions(year, 999) if meeting and p.startswith(meeting) and p != si.get('Path')]
    if done:
        return done[0]
    for y in (year - 1, year - 2):
        try:
            paths = data.circuit_sessions(y, si['Meeting']['Circuit']['Key'])
        except Exception:
            continue
        if paths:
            return paths[-1]
    return None


def learned_positions(path, track):
    """learn_positions(path, track), cached on disk per source session and outline (archived sessions never change)."""
    f = data.CACHE / f"positions_v5_{path.replace('/', '_')}_{len(track.xy)}_{int(track.s[-1])}.json"
    if f.exists():
        return json.loads(f.read_text())
    r = learn_positions(path, track)
    data.CACHE.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(r))
    return r


def learn_positions(path, track):
    """From an archived session with GPS: {'sectors': per sector, the lap fraction where each of its timing
    mini-sectors ends (median GPS spot of cars as they complete it), 'profile': lap fraction reached at each 1 % of a
    green lap's time}. Measured on `track`, the outline the estimates are drawn on (fractions of another outline would
    not line up). With both, the no-GPS map was 18 m off the real car (median; 78 m p90) on the 2026 Bahrain race
    learning from its qualifying, against 227 m (482 m) with equal mini-sectors and constant speed."""
    st = State()
    msgs = ReplayFeed(st, path).messages(skip=('TeamRadio', 'CarData.z'))  # not needed here, CarData is the biggest
    tr = track
    pts, L = tr.xy[:-1], tr.s[-1]

    def frac(x, y, prev):
        """Lap fraction of the outline point nearest (x, y). Where the track crosses itself (Suzuka) the other branch
        can be nearest, so only points within 8 % of a lap of the car's last spot count, when there is one."""
        d = np.hypot(pts[:, 0] - x, pts[:, 1] - y)
        if prev is not None:
            d = np.where(np.abs((tr.s[:-1] / L - prev + 0.5) % 1 - 0.5) < 0.08, d, np.inf)
            if not np.isfinite(d.min()):
                d = np.hypot(pts[:, 0] - x, pts[:, 1] - y)
        return tr.s[np.argmin(d)] / L
    gps, events = {}, []  # car -> ([t], [lap fraction]);  (car, t, global mini-sector index)
    for t, topic, x in msgs:
        if topic == 'Position.z':
            for n, e in unz(x)['Position'][-1]['Entries'].items():
                if e['X'] or e['Y']:
                    g = gps.setdefault(n, ([], []))
                    g[0].append(t)
                    g[1].append(frac(e['X'], e['Y'], g[1][-1] if g[1] and t - g[0][-1] < 5 else None))
            continue
        if topic == 'TimingData' and isinstance(x, dict):
            for n, ln in (x.get('Lines') or {}).items():
                for si_, sec in (ln.get('Sectors').items() if isinstance(ln.get('Sectors'), dict) else ()):
                    segs = sec.get('Segments') if isinstance(sec, dict) else None
                    for k, v in (segs.items() if isinstance(segs, dict) else ()):
                        if isinstance(v, dict) and v.get('Status'):
                            events.append((n, t, int(si_), int(k)))
        st.apply(topic, x, t)
    counts = next(([len(seq(sec.get('Segments'))) for sec in seq(ln.get('Sectors'))]
                   for ln in st.d.get('TimingData', {}).get('Lines', {}).values() if seq(ln.get('Sectors'))), [])
    G = sum(counts)

    def at(n, t):  # the car's GPS lap fraction at feed time t (nearest sample within 1 s)
        ts, fs = gps.get(n, ((), ()))
        i = np.searchsorted(ts, t)
        near = [j for j in (i - 1, i) if 0 <= j < len(ts) and abs(ts[j] - t) < 1]
        return fs[min(near, key=lambda j: abs(ts[j] - t))] if near else None

    def circular_median(v, ref):
        d = (np.array(v) - ref + 0.5) % 1 - 0.5
        return float((ref + np.median(d)) % 1)

    spots = [[] for _ in range(G)]
    for n, t, sec, k in events:
        g = sum(counts[:sec]) + k
        f = at(n, t)
        if g < G and f is not None:
            spots[g].append(f)
    bounds = []
    for g, v in enumerate(spots):
        if len(v) < 5:
            return {'sectors': None, 'profile': None}
        bounds.append(1.0 if g == G - 1 else circular_median(v, np.angle(np.mean(np.exp(2j * np.pi * np.array(v)))) / (2 * np.pi) % 1))
    ordered = all(b > a - 0.01 for a, b in zip(bounds, bounds[1:]))  # clearly out of order: don't trust it
    for g in range(1, G):  # two loops at one spot (Suzuka has a pair at 0.985): keep them strictly in order
        bounds[g] = min(max(bounds[g], bounds[g - 1] + 1e-4), 1.0)
    sectors = [bounds[sum(counts[:i]):sum(counts[:i + 1])] for i in range(len(counts))] if ordered else None

    green = [l for l in st.laps if l['t'] and not l['pit'] and not l['sc']]
    med = np.median([l['t'] for l in green]) if green else 0
    bins = [[] for _ in range(101)]
    for l in green:
        if l['t'] > med * 1.07 or l['drv'] not in gps:
            continue
        ts, fs = gps[l['drv']]
        t0 = l['at'] - l['t']
        i, j = np.searchsorted(ts, t0), np.searchsorted(ts, l['at'])
        for a, f in zip(ts[i:j], fs[i:j]):
            bins[int(round((a - t0) / l['t'] * 100))].append(f)
    tau = np.linspace(0, 1, 101)
    prof = np.array([circular_median(v, tau[i]) if v else np.nan for i, v in enumerate(bins)])
    prof[0], prof[-1] = 0.0, 1.0
    ok = ~np.isnan(prof)
    prof = np.maximum.accumulate(np.clip(np.interp(tau, tau[ok], prof[ok]), 0, 1))
    return {'sectors': sectors, 'profile': [round(float(v), 4) for v in prof], 'source': path}


def load_pit_loss(state, si):
    """Pit loss known before this session's own stops: circuit API (also its catalogue, which covers years the
    per-year endpoint 404s on), else a sprint earlier this weekend, plus the catalogue median as a last resort."""
    from .sim import pit_loss_samples
    key, year = str(si['Meeting']['Circuit']['Key']), int(si['StartDate'][:4])
    try:
        cat = data.circuit_catalog()
    except Exception as e:
        print('circuit catalogue unavailable:', e)
        cat = {}
    entry = (cat.get(key) or {}).get('pitLoss') or {}
    state.pit_known = (state.track.pit_loss if state.track else None) or (
        {k: float(v) for k, v in entry.items()} if entry.get('normal') else None)
    typical = [float(c['pitLoss']['normal']) for c in cat.values() if (c.get('pitLoss') or {}).get('normal')]
    state.pit_typical = float(np.median(typical)) if typical else None
    if state.pit_known:
        return
    meeting = si.get('Path', '').rsplit('/', 2)[0]
    for label, p in data.past_sessions(year, n=999):
        if meeting and p.startswith(meeting) and p != si.get('Path') and label.endswith(' - Sprint'):
            st, laps = laps_from_archive(p, track=False)
            s = pit_loss_samples(laps, st.stops())
            if len(s) >= 3:
                state.pit_weekend = {'normal': float(np.median(s)), 'source': 'sprint', 'n': len(s)}


def outline_from_archive(path):
    """Position.z trace of the fastest clean lap in an archived session (lap start = finish line)."""
    from .sim import clean
    _, laps = laps_from_archive(path, track=False)
    best = min(clean(laps), key=lambda l: l['t'])
    t0, t1 = best['at'] - best['t'], best['at']
    xy = [(e['X'], e['Y']) for t, z in data.archive_stream(path, 'Position.z') if t0 <= t <= t1
          for sample in unz(z)['Position'] for n, e in sample['Entries'].items() if n == best['drv'] and (e['X'] or e['Y'])]
    return {'x': [p[0] for p in xy], 'y': [p[1] for p in xy]}


# ---- feeds -----------------------------------------------------------------
class ReplayFeed(threading.Thread):
    """Plays an archived session through the same State, starting just before the session goes green.
    Can pause (speed 0) and jump to any time or lap (seek / seek_lap)."""
    TOPICS = ['SessionInfo', 'DriverList', 'TimingAppData', 'TimingData', 'TrackStatus', 'LapCount', 'SessionStatus',
              'RaceControlMessages', 'WeatherData', 'PitStopSeries', 'ExtrapolatedClock', 'ChampionshipPrediction',
              'TeamRadio', 'CarData.z', 'Position.z']  # car streams last: dropped without GPS (F1 TV only live)

    def __init__(self, state, path, speed=1.0, gps=True):
        super().__init__(daemon=True)
        self.state, self.path, self.speed, self.gps = state, path, speed, gps
        self.t0, self.w0, self.stop = 0, time.monotonic(), False
        self.begin = self.end = 0    # feed seconds: 30 s before the green flag, last message (not .start: Thread.start)
        self.lap_starts = {}         # lap -> feed time the leader started it (LapCount)
        self.seek_to = None          # pending jump, handled by the feed thread
        self.status = 'downloading…'
        state.feed = self

    def now(self):
        return min(self.t0 + (time.monotonic() - self.w0) * self.speed, self.end or float('inf'))

    def set_speed(self, s):
        """0 pauses."""
        self.t0, self.w0, self.speed = self.now(), time.monotonic(), s
        self.status = 'replay paused' if s == 0 else f'replay {s:g}x'

    def seek(self, t):
        self.seek_to = min(max(t, self.begin), self.end)

    def seek_lap(self, lap):
        """Jump to just after the leader starts `lap` (races/sprints; other sessions have no laps)."""
        laps = [n for n in self.lap_starts if n <= lap]
        self.seek(self.lap_starts[max(laps)] + 0.5 if laps else self.begin)

    def messages(self, skip=()):
        topics = [t for t in (self.TOPICS if self.gps else self.TOPICS[:-2]) if t not in skip]
        return sorted(((t, topic, x) for topic in topics for t, x in data.archive_stream(self.path, topic)),
                      key=lambda m: m[0])

    def run(self):
        msgs = self.messages()
        si = next((x for t, topic, x in msgs if topic == 'SessionInfo'), None)
        if si:
            load_track(self.state, si, positions=not self.gps)
        self.begin = next((t for t, topic, x in msgs if topic == 'SessionStatus' and x.get('Status') == 'Started'), 0) - 30
        self.end = msgs[-1][0] if msgs else 0
        for t, topic, x in msgs:
            if topic == 'LapCount' and x.get('CurrentLap'):
                self.lap_starts.setdefault(int(x['CurrentLap']), t)
        self.t0, self.w0 = self.begin, time.monotonic()
        self.set_speed(self.speed)
        i, q = 0, self.state.q
        while not self.stop:
            if self.seek_to is not None:
                target, self.seek_to = self.seek_to, None
                if i and target < msgs[i - 1][0]:  # backwards: rebuild the session from the start
                    q.put((RESET, None, target))
                    i = 0
                # fast-forward without waiting. Car streams only for the last 10 s: unpacking every one takes seconds
                while i < len(msgs) and msgs[i][0] <= target:
                    if msgs[i][1] not in ('Position.z', 'CarData.z') or msgs[i][0] > target - 10:
                        q.put((msgs[i][1], msgs[i][2], msgs[i][0]))
                    i += 1
                self.t0, self.w0 = target, time.monotonic()
                self.set_speed(self.speed)  # status back from 'finished'
            elif i < len(msgs) and msgs[i][0] <= self.now():
                q.put((msgs[i][1], msgs[i][2], msgs[i][0]))
                i += 1
            else:  # waiting for the next message, paused, or finished (can still jump back)
                if i >= len(msgs):
                    self.status = 'replay finished'
                time.sleep(0.1 if not self.speed or i >= len(msgs) else min(max(msgs[i][0] - self.now(), 0) / self.speed, 0.1))


class LiveFeed(threading.Thread):
    """F1 live timing over SignalR via FastF1's client. Reconnects forever (the server drops after ~2 h)."""

    def __init__(self, state, auth=True, record=False):
        super().__init__(daemon=True)
        self.state, self.auth, self.stop = state, auth, False
        # record: one process at a time (the dashboard, else the tray). state None: only records (the tray)
        self.record, self.key, self.out = record, None, None
        self.status = 'connecting…'
        if state:
            state.feed = self

    def now(self):
        return time.time()

    def push(self, topic, x):
        t = time.time()
        if topic == 'SessionInfo' and isinstance(x, dict) and x.get('Key') and str(x['Key']) != self.key:
            self.key = str(x['Key'])
            if self.record:
                self.restore()
        if self.state and topic == 'SessionInfo' and self.state.track is None and isinstance(x, dict) and 'Meeting' in x:
            load_track(self.state, x, positions=True)
        if self.state:
            self.state.q.put((topic, x, t))
        if self.out and not topic.endswith('.z'):  # GPS and car data: only the latest matters, and they're most of it
            self.out.write(json.dumps([t, topic, x]) + '\n')

    def restore(self):
        """Opened again during a session: what this session sent before (the lap log for the sim, gaps, pit stops)
        goes in first, then the server's snapshot of now. Records the rest of it to the same file."""
        folder = data.APP_DIR / 'sessions'
        folder.mkdir(parents=True, exist_ok=True)
        for f in folder.glob('*.jsonl'):  # a weekend's sessions are enough
            if f.stat().st_mtime < time.time() - 4 * 86400:
                f.unlink()
        path = folder / f'{self.key}.jsonl'
        if self.state and path.exists() and not self.state.d and self.state.q.empty():  # not when reconnecting into a running State
            for line in path.open(encoding='utf-8'):
                try:
                    t, topic, x = json.loads(line)
                except ValueError:
                    continue  # last line cut off when the app was killed
                self.state.q.put((topic, x, t))
        if self.out:
            self.out.close()
        self.out = path.open('a', encoding='utf-8', buffering=1)

    def run(self):
        from fastf1.livetiming.client import SignalRClient
        from signalrcore.messages.completion_message import CompletionMessage
        feed = self

        class Client(SignalRClient):
            def _on_message(self, msg):
                super()._on_message(msg)  # keeps FastF1's raw recording in live.txt
                if feed.stop:
                    return
                if isinstance(msg, CompletionMessage):  # the snapshot: SessionInfo first, it says what to restore
                    for k, v in sorted((msg.result or {}).items(), key=lambda kv: kv[0] != 'SessionInfo'):
                        feed.push(k, v)
                elif isinstance(msg, list) and len(msg) >= 2:
                    feed.push(msg[0], msg[1])

            def _run(self):
                """FastF1 3.8.3 passes access_token_factory=None without auth, and signalrcore 1.0 refuses that
                ('access_token_factory is not function'). Same connection, with the option left out."""
                if not self._no_auth:
                    return super()._run()
                import logging
                import requests
                from signalrcore.hub_connection_builder import HubConnectionBuilder
                self._output_file = open(self.filename, self.filemode)
                r = requests.options(self._negotiate_url, headers=self.headers)
                self.headers.update({'Cookie': f"AWSALBCORS={r.cookies['AWSALBCORS']}"})
                self._connection = HubConnectionBuilder().with_url(
                    self._connection_url, options={'verify_ssl': True, 'headers': self.headers}
                ).configure_logging(logging.INFO).build()
                self._connection.on_open(self._on_connect)
                self._connection.on_close(self._on_close)
                self._connection.on('feed', self._on_message)
                self._connection.start()
                t = time.time()
                while not self._is_connected and not feed.stop:
                    if time.time() - t > 30:
                        raise ConnectionError('no answer from the live timing server')
                    time.sleep(0.1)
                self._connection.send('Subscribe', [self.topics], on_invocation=self._on_message)

            def _supervise(self):
                """FastF1's loop only ends after `timeout` s without data, which never happens in a session.
                Also end it when the feed is stopped (e.g. restarted with F1 TV auth)."""
                self._t_last_message = time.time()
                while not feed.stop and time.time() - self._t_last_message <= self.timeout:
                    time.sleep(1)
                self._exit()

        while not self.stop:
            use_auth = self.auth and token_ok()
            self.status = 'live (F1 TV)' if use_auth else 'live (no auth: estimated map)'
            data.APP_DIR.mkdir(parents=True, exist_ok=True)
            c = Client(str(data.APP_DIR / 'live.txt'), filemode='a', timeout=120, no_auth=not use_auth)
            c.topics += ['PitStopSeries', 'TyreStintSeries', 'ChampionshipPrediction']
            try:
                c.start()  # blocks until no data for `timeout` seconds
            except Exception as e:
                self.status = f'disconnected: {e}'
            time.sleep(15)
        if self.out:
            self.out.close()


# ---- F1 TV sign-in (FastF1's browser helper, without its console prompts) ------
def token_ok():
    from fastf1.internals import f1auth
    try:
        f1auth._verify_jwt(f1auth.AUTH_DATA_FILE.read_text().strip(), f1auth.JWKS_URL)
        return True
    except Exception:
        return False


SIGNIN_HELP = ('Needs the "FastF1 Companion" browser extension (the page links it). Log in at formula1.com, '
               'then click "Connect FastF1" on the page the extension opens.')
_signin_port = None  # port of a sign-in still waiting for the browser


def sign_in(timeout=1800):
    """F1 TV sign-in through FastF1's browser flow: the FastF1 Companion extension reads the formula1.com login cookie
    and POSTs it to http://localhost:<port>/auth. Blocks until that arrives or `timeout` s (installing the extension
    and logging in can take a while). Returns True, or a string saying what went wrong."""
    global _signin_port
    from fastf1.internals import f1auth  # ponytail: private FastF1 internals, re-check on FastF1 upgrades
    if _signin_port:  # clicked again while waiting: same server, reopen the page
        webbrowser.open(f'https://f1login.fastf1.dev?port={_signin_port}')
        f1auth._auth_finished.wait(timeout)
        return token_ok() or 'sign-in did not finish'

    class Quiet(f1auth.AuthHandler):
        def log_message(self, *args):  # logs to sys.stderr, which is None under pythonw.exe: the reply never went out
            pass

    class V6(HTTPServer):
        address_family = socket.AF_INET6

    servers = [HTTPServer(('127.0.0.1', 0), Quiet)]
    _signin_port = port = servers[0].server_port
    try:  # the extension calls 'localhost', which can resolve to ::1 first
        servers.append(V6(('::1', port), Quiet))
    except OSError:
        pass
    for s in servers:
        threading.Thread(target=s.serve_forever, daemon=True).start()
    f1auth._auth_finished.clear()
    f1auth._subscription_token = None
    webbrowser.open(f'https://f1login.fastf1.dev?port={port}')
    try:
        arrived = f1auth._auth_finished.wait(timeout)
    finally:
        for s in servers:
            s.shutdown()
            s.server_close()
        _signin_port = None
    if not arrived:
        return f'no answer from the browser within {timeout // 60} min'
    if not f1auth._subscription_token:
        return 'logged in, but the account has no F1 TV subscription token'
    f1auth.AUTH_DATA_FILE.write_text(f1auth._subscription_token)
    return token_ok() or 'token received but it did not verify'


def laps_from_archive(path, track=True):
    """Run an archived session through State with no sleeping; returns (state, laps). Used by the sim + backtest."""
    st = State()
    feed = ReplayFeed(st, path, gps=False)
    msgs = feed.messages()
    si = next((x for t, topic, x in msgs if topic == 'SessionInfo'), None)
    if si and track:
        load_track(st, si)
    for t, topic, x in msgs:
        st.apply(topic, x, t)
    return st, st.laps
