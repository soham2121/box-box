"""Live timing: SignalR feed (F1 TV or no auth), archive replay, state merge, lap log, estimated car positions."""
import base64
import json
import queue
import threading
import time
import webbrowser
import zlib
from http.server import HTTPServer

import numpy as np

from . import data


NEUTRALISED = {'4', '5', '6', '7'}  # TrackStatus: SC, red flag, VSC, VSC ending ('2' is just a local yellow)


def merge(base, upd):
    """Apply an F1 live timing delta: dicts merge recursively, {"3": ...} patches list index 3."""
    if not isinstance(upd, dict) or not isinstance(base, (dict, list)):
        return upd
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


class Track:
    def __init__(self, info):
        xy = np.c_[info['x'], info['y']].astype(float)
        self.xy = np.vstack([xy, xy[:1]])  # close the loop
        self.s = np.r_[0, np.cumsum(np.hypot(*np.diff(self.xy, axis=0).T))]
        self.ms = info.get('miniSectorsIndexes') or []
        self.rotation = info.get('rotation', 0)
        self.pit_loss = {k: float(v) for k, v in (info.get('pitLoss') or {}).items()}
        self.corners = [(c['number'], c['trackPosition']['x'], c['trackPosition']['y']) for c in info.get('corners', [])]

    def at(self, f):
        d = (f % 1) * self.s[-1]
        return np.interp(d, self.s, self.xy[:, 0]), np.interp(d, self.s, self.xy[:, 1])

    def bounds(self, n):
        """Lap fraction at the end of each of n timing mini-sectors."""
        if len(self.ms) == n:
            return self.s[self.ms] / self.s[-1]
        return np.arange(1, n + 1) / n  # ponytail: assumes equal mini-sector lengths when the circuit API has no indexes


class State:
    """Everything the widgets read. Feeds push into .q from their threads; pump() applies on the UI thread."""

    def __init__(self):
        self.q = queue.Queue()
        self.d = {}            # topic -> merged data
        self.laps = []         # completed laps, see _lap()
        self.gps = {}          # racing number -> (x, y), only when Position.z flows (F1 TV / replay)
        self.gps_t = 0
        self.prog = {}         # racing number -> [lap fraction at snap, snap time, cap fraction]
        self.flags = {}        # racing number -> {'pit', 'sc'} seen during the current lap
        self.lastlap = {}
        self.track = None
        self.feed = None
        self.t = 0             # feed clock of the last message

    def pump(self, limit=5000):
        for _ in range(limit):
            try:
                self.apply(*self.q.get_nowait())
            except queue.Empty:
                return

    def now(self):
        return self.feed.now() if self.feed else self.t

    def apply(self, topic, x, t):
        self.t = t
        if topic.endswith('.z'):
            topic, x = topic[:-2], unz(x)
        if topic == 'Position':
            if x.get('Position'):
                self.gps = {n: (e['X'], e['Y']) for n, e in x['Position'][-1]['Entries'].items() if e.get('X') or e.get('Y')}
                self.gps_t = t
            return
        if topic == 'CarData':
            if x.get('Entries'):
                self.d['CarData'] = x['Entries'][-1]['Cars']
            return
        if topic == 'TrackStatus' and x.get('Status') in NEUTRALISED:
            for f in self.flags.values():
                f['sc'] = True
        if topic == 'TimingData':
            return self._timing(x, t)
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
            if ln.get('InPit') or ln.get('PitOut'):
                f['pit'] = True
            if 'NumberOfLaps' in ln:
                self._lap(n, line)
                self.prog[n] = [0.0, t, self._cap(n, line, 0)]
            elif n in passed and self.track:
                counts = [len(seq(s.get('Segments'))) for s in seq(line.get('Sectors'))]
                s, k = passed[n]
                g = sum(counts[:s]) + k
                b = self.track.bounds(sum(counts))
                if g < len(b):
                    self.prog[n] = [b[g], t, b[g + 1] if g + 1 < len(b) else 1.0]

    def _cap(self, n, line, g):
        if not self.track:
            return 1.0
        b = self.track.bounds(sum(len(seq(s.get('Segments'))) for s in seq(line.get('Sectors'))) or 1)
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
        self.flags[n] = {'pit': bool(line.get('InPit')), 'sc': self.d.get('TrackStatus', {}).get('Status') in NEUTRALISED}

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
        if self.gps and self.now() - self.gps_t < 10 * getattr(self.feed, 'speed', 1):  # replays pump less often in feed time
            return [(n, x, y, bool(lines.get(n, {}).get('InPit'))) for n, (x, y) in self.gps.items()], True
        if not self.track:
            return [], False
        now, out = self.now(), []
        for n, (f0, t0, cap) in self.prog.items():
            ln = lines.get(n, {})
            if ln.get('Retired') or ln.get('Stopped'):
                continue
            lap_t = secs((ln.get('LastLapTime') or {}).get('Value')) or secs((ln.get('BestLapTime') or {}).get('Value')) or 100
            f = f0 if ln.get('InPit') else min(f0 + (now - t0) / lap_t, cap)
            out.append((n, *self.track.at(f), bool(ln.get('InPit'))))
        return out, False

    def session(self):
        si = self.d.get('SessionInfo', {})
        lc = self.d.get('LapCount', {})
        return {'name': f"{si.get('Meeting', {}).get('Name', '')} {si.get('Name', '')}".strip(),
                'type': si.get('Type', ''), 'lap': lc.get('CurrentLap', 0), 'total': lc.get('TotalLaps', 0),
                'track': self.d.get('TrackStatus', {}).get('Message', ''), 'status': self.d.get('SessionStatus', {}).get('Status', '')}


def load_track(state, si):
    """Outline for a SessionInfo payload (feed thread; network). New circuits fall back to an archived lap trace."""
    try:
        state.track = Track(data.circuit(si['Meeting']['Circuit']['Key'], int(si['StartDate'][:4])))
        return
    except Exception as e:
        print('circuit API has no outline:', e)
    meeting = si.get('Path', '').rsplit('/', 2)[0]
    done = [p for _, p in data.past_sessions(int(si['StartDate'][:4]), n=999) if meeting and p.startswith(meeting)]
    if done:
        try:
            state.track = Track(outline_from_archive(done[-1]))  # earliest finished session of this weekend
        except Exception as e:  # map stays empty; timing still works
            print('outline fallback failed:', e)


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
    """Plays an archived session through the same State, starting just before the session goes green."""
    TOPICS = ['SessionInfo', 'DriverList', 'TimingAppData', 'TimingData', 'TrackStatus', 'LapCount', 'SessionStatus',
              'RaceControlMessages', 'WeatherData', 'PitStopSeries', 'Position.z']

    def __init__(self, state, path, speed=1.0, gps=True):
        super().__init__(daemon=True)
        self.state, self.path, self.speed, self.gps = state, path, speed, gps
        self.t0, self.w0, self.stop = 0, time.monotonic(), False
        self.status = 'downloading…'
        state.feed = self

    def now(self):
        return self.t0 + (time.monotonic() - self.w0) * self.speed

    def set_speed(self, s):
        self.t0, self.w0, self.speed = self.now(), time.monotonic(), s
        self.status = f'replay {s:g}x'

    def messages(self):
        topics = self.TOPICS if self.gps else self.TOPICS[:-1]
        return sorted(((t, topic, x) for topic in topics for t, x in data.archive_stream(self.path, topic)),
                      key=lambda m: m[0])

    def run(self):
        msgs = self.messages()
        si = next((x for t, topic, x in msgs if topic == 'SessionInfo'), None)
        if si:
            load_track(self.state, si)
        start = next((t for t, topic, x in msgs if topic == 'SessionStatus' and x.get('Status') == 'Started'), 0) - 30
        self.t0, self.w0 = start, time.monotonic()
        self.status = f'replay {self.speed:g}x'
        for t, topic, x in msgs:
            while not self.stop and t > self.now():
                time.sleep(min(max(t - self.now(), 0) / self.speed, 0.1))
            if self.stop:
                return
            self.state.q.put((topic, x, t))
        self.status = 'replay finished'


class LiveFeed(threading.Thread):
    """F1 live timing over SignalR via FastF1's client. Reconnects forever (the server drops after ~2 h)."""

    def __init__(self, state, auth=True):
        super().__init__(daemon=True)
        self.state, self.auth, self.stop = state, auth, False
        self.status = 'connecting…'
        state.feed = self

    def now(self):
        return time.time()

    def push(self, topic, x):
        if topic == 'SessionInfo' and self.state.track is None and isinstance(x, dict) and 'Meeting' in x:
            load_track(self.state, x)
        self.state.q.put((topic, x, time.time()))

    def run(self):
        from fastf1.livetiming.client import SignalRClient
        from signalrcore.messages.completion_message import CompletionMessage
        feed = self

        class Client(SignalRClient):
            def _on_message(self, msg):
                super()._on_message(msg)  # keeps FastF1's raw recording in live.txt
                if isinstance(msg, CompletionMessage):
                    for k, v in (msg.result or {}).items():
                        feed.push(k, v)
                elif isinstance(msg, list) and len(msg) >= 2:
                    feed.push(msg[0], msg[1])

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


# ---- F1 TV sign-in (FastF1's browser helper, without its console prompts) ------
def token_ok():
    from fastf1.internals import f1auth
    try:
        f1auth._verify_jwt(f1auth.AUTH_DATA_FILE.read_text().strip(), f1auth.JWKS_URL)
        return True
    except Exception:
        return False


def sign_in(timeout=300):
    """Opens the browser login; blocks until the token arrives. Returns True on success."""
    from fastf1.internals import f1auth  # ponytail: private FastF1 internals, re-check on FastF1 upgrades
    srv = HTTPServer(('127.0.0.1', 0), f1auth.AuthHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    f1auth._auth_finished.clear()
    webbrowser.open(f'https://f1login.fastf1.dev?port={srv.server_port}')
    f1auth._auth_finished.wait(timeout)
    srv.shutdown()
    if f1auth._subscription_token:
        f1auth.AUTH_DATA_FILE.write_text(f1auth._subscription_token)
    return token_ok()


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
