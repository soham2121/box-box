"""Race and sprint alerts for my drivers and my team, from the live session as shown (after the delay).
Delivered as a popup in the dashboard while it's open on screen, else as a Windows notification from the tray."""
import re

from . import sim

# alert type -> label in Settings; each can be switched off (config 'alerts')
ALERTS = {
    'pit_window': 'Best pit lap in 2 laps (strategy sim)',
    'rival_pit': 'Car just ahead or behind pitted',
    'neutral': 'Safety car / VSC out: cheap stop',
    'steward': 'Investigation, penalty or retirement',
    'places': 'Lost or gained 2+ places in a lap',
    'double_stack': 'Team: double stack',
    'team_fight': 'Team: teammates within 1 s or swapping places',
    'slow_stop': 'Team: slow pit stop',
}
SLOW_STOP_S = 2.0  # time lost beyond a normal stop: about a 4.5 s stop instead of 2.5 s (lap times are noisy below that)


def snapshot(st):
    """What the alerts compare from one check to the next."""
    msgs = st.d.get('RaceControlMessages', {}).get('Messages')
    rc = [m for m in (msgs.values() if isinstance(msgs, dict) else msgs or []) if isinstance(m, dict)]
    return {'ds': {d['code']: d for d in st.drivers() if d['pos'] < 99}, 'status': str(st.d.get('TrackStatus', {}).get('Status')),
            'rc': len(rc), 'rc_msgs': rc, 'lap': st.session()['lap'], 'laps': len(st.laps)}


def events(prev, cur, laps, mine, team, loss, stops=()):
    """[(key, type, title, text)] that happened between two snapshots. mine: codes of my drivers with alerts on;
    team: codes of my team's cars (team alerts on), loss: sim.pit_loss() of the session, stops: State.stops()."""
    out, ds, was = [], cur['ds'], prev['ds']
    num = {c: d['drv'] for c, d in ds.items()}
    gap = lambda a, b: _pair_gap(ds, a, b)

    for m in mine:  # a car just ahead or behind me pitted
        me = was.get(m)
        if not me or me['pit'] or me['out']:
            continue
        for o in was.values():
            if abs(o['pos'] - me['pos']) == 1 and o['code'] in ds and ds[o['code']]['pit'] and not o['pit']:
                g = gap(m, o['code'])
                near = f", {g:.1f}s {'ahead' if o['pos'] < me['pos'] else 'behind'}" if g is not None else ''
                text = (f"{o['code']} (P{o['pos']}{near}) pitted: undercut threat, cover it?" if o['pos'] > me['pos'] else
                        f"{o['code']} (P{o['pos']}{near}) pitted: stay out for the overcut, or follow?")
                out.append((('rival_pit', m, o['code'], o['stops']), 'rival_pit', f'{m}: car {"behind" if o["pos"] > me["pos"] else "ahead"} pitted', text))

    if cur['status'] in ('4', '6') and prev['status'] != cur['status'] and mine:  # SC / VSC out
        kind = 'SC' if cur['status'] == '4' else 'VSC'
        cost = loss['sc' if kind == 'SC' else 'vsc']
        rows = [d for d in ds.values() if not d['out']]
        lines = []
        for m in mine:
            if m in ds and not ds[m]['out']:
                pos = sim.rejoin(rows, ds[m]['drv'], cost)[0]
                lines.append(f"{m} P{ds[m]['pos']} would rejoin P{pos}")
        out.append((('neutral', cur['status'], cur['lap']), 'neutral', f'{"Safety car" if kind == "SC" else "VSC"} out: cheap stop',
                    f"A stop costs ~{cost:.0f}s now (vs {loss['normal']:.0f}s). " + '; '.join(lines)))

    watch = set(mine) | set(team)
    for msg in cur['rc_msgs'][prev['rc']:]:  # investigations and penalties for my cars
        text = str(msg.get('Message', ''))
        if not re.search(r'INVESTIGATION|PENALTY', text):
            continue
        for c in watch:
            if c in num and re.search(rf'\bCAR {num[c]}\b', text):
                out.append((('steward', text), 'steward', f'{c}: stewards', text.capitalize()))
    for c in watch:  # retired
        if c in ds and ds[c]['out'] and c in was and not was[c]['out']:
            out.append((('retired', c), 'steward', f'{c} out of the race', f"{c} has stopped (was P{was[c]['pos']})."))

    by = {}
    for l in laps[prev['laps']:]:  # laps completed since: places won or lost
        by.setdefault(l['drv'], []).append(l)
    for m in mine:
        for l in by.get(num.get(m), []):
            before = next((p for p in reversed(laps) if p['drv'] == l['drv'] and p['lap'] == l['lap'] - 1), None)
            if before and l['lap'] > 1 and not l['pit'] and before['pos'] and l['pos'] and abs(before['pos'] - l['pos']) >= 2:
                won = before['pos'] - l['pos']
                out.append((('places', m, l['lap']), 'places', f"{m} {'gained' if won > 0 else 'lost'} {abs(won)} places",
                            f"Lap {l['lap']}: P{before['pos']} → P{l['pos']}"))

    cars = [c for c in team if c in ds]
    if len(cars) == 2:
        a, b = sorted(cars, key=lambda c: ds[c]['pos'])
        if ds[a]['pit'] and ds[b]['pit'] and not (was.get(a, {}).get('pit') and was.get(b, {}).get('pit')):
            out.append((('double', cur['lap']), 'double_stack', 'Double stack', f'{a} and {b} are in the pits together '
                                                                                  f'(lap {cur["lap"]}).'))
        if a in was and b in was:
            pa, pb = sorted((a, b), key=lambda c: was[c]['pos'])
            if pa != a and not ds[a]['pit'] and not ds[b]['pit']:
                out.append((('swap', a, b, cur['lap']), 'team_fight', 'Teammates swapped', f'{a} is now ahead of {b} (P{ds[a]["pos"]}).'))
            g, g0 = gap(a, b), _pair_gap(was, a, b)
            close = abs(ds[a]['pos'] - ds[b]['pos']) == 1 and g is not None and g < 1.0
            if close and not (g0 is not None and g0 < 1.0 and abs(was[a]['pos'] - was[b]['pos']) == 1):
                # once in 10 laps: a gap hovering around 1 s would repeat it every lap
                out.append((('fight', cur['lap'] // 10), 'team_fight', 'Teammates fighting',
                            f'{b} is {g:.1f}s behind {a} (P{ds[b]["pos"]}).'))

    for c in team:  # a team stop clearly slower than a normal one, once its out-lap is done
        if c not in num:
            continue
        for s in stops:
            if s['drv'] != num[c] or not any(l['drv'] == s['drv'] and l['lap'] == s['lap'] + 1 for l in laps[prev['laps']:]):
                continue
            if s.get('stationary'):
                slow, text = s['stationary'] > 4.0, f"{c} stood {s['stationary']:.1f}s in the box (lap {s['lap']})."
            else:
                lost = sim.pit_loss_samples(laps, [s])
                slow = bool(lost) and lost[0] - loss['normal'] > SLOW_STOP_S
                text = f"{c} lost ~{lost[0] - loss['normal']:.0f}s more than a normal stop (lap {s['lap']}, from lap times)." if lost else ''
            if slow:
                out.append((('slow', c, s['lap']), 'slow_stop', f'{c}: slow pit stop', text))
    return out


def _pair_gap(ds, a, b):
    """Seconds between two cars from their gaps to the leader (the leader's is '' or 'LAP n'); None if either is
    lapped ('1L')."""
    lead = lambda g: 0.0 if not str(g).strip() or str(g).startswith('LAP') else sim._num(g)
    ga, gb = lead(ds[a]['gap']), lead(ds[b]['gap'])
    return abs(ga - gb) if ga is not None and gb is not None else None


class Watch:
    """Checks the live session every tick (App.tick) and sends new alerts once each."""

    def __init__(self, app):
        self.app, self.st, self.prev, self.sent, self.window_job = app, None, None, set(), None

    def check(self):
        app, st = self.app, self.app.live
        if st is not self.st:  # a new live session: nothing before now counts
            self.st, self.prev, self.sent = st, None, set()
        if not st or st.session()['type'] not in ('Race', 'Sprint'):
            return
        if st.t < st.now() - 15:  # still catching up (a restored recording): history, not news
            self.prev = None
            return
        cfg = app.cfg
        on = cfg.get('alerts') or {}
        mine = [d['code'] for d in cfg.get('my_drivers') or () if d.get('alerts')]
        team = (cfg.get('my_team') or {})
        from .widgets import team_codes
        team = team_codes(app, st) if team.get('alerts') else set()
        cur = snapshot(st)
        if self.prev and (mine or team):
            loss = st.pit_loss(app.model())
            for key, kind, title, text in events(self.prev, cur, st.laps, mine, team, loss, st.stops()):
                if on.get(kind, True) and key not in self.sent:
                    self.sent.add(key)
                    app.notify(title, text)
            if on.get('pit_window', True) and cur['lap'] != self.prev['lap']:
                self.pit_window(st, mine, cur['lap'])
        self.prev = cur

    def pit_window(self, st, mine, lap):
        """Once a lap, in the background: each of my drivers' best stop lap (strategy sim); alert 2 laps before it."""
        from .widgets import bg
        s = st.session()
        if self.window_job or not mine or not s['total'] or s['total'] - lap < 5:
            return
        m = {**self.app.model(), 'two_compounds': 'Sprint' not in s['name']}
        laps, ds, loss = list(st.laps), [d for d in st.drivers() if not d['out']], st.pit_loss(m)
        names = sim.relative_compounds(laps)
        laps, ds = sim.relabel(laps, names), sim.relabel(ds, names)
        todo = [d for d in ds if d['code'] in mine and sim.stops_owed(d, s['total'] - lap, m)]
        if not todo:
            return

        def work():
            model = sim.fit(laps, s['total'], m)
            return [(d['code'], sim.best_window(ds, lap, model, d['drv'], runs=60, pit_loss=loss, lap_s=sim.lap_time(laps))[0])
                    for d in todo]

        def done(res):
            self.window_job = None
            if isinstance(res, Exception) or st is not self.st:
                return
            for code, (mean, best, cmp) in res:
                key = ('pit_window', code, best)
                if best == lap + 2 and key not in self.sent:
                    self.sent.add(key)
                    self.app.notify(f'{code}: pit in 2 laps', f'Best stop: lap {best} on {cmp.title()} '
                                                              f'(strategy sim, mean finish P{mean:.1f}).')
        self.window_job = True
        bg(work, done)
