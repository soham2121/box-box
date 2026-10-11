"""Box Box tray app: config, background fetches, feeds, the widget windows and the live dashboard.
Run: python -m boxbox.main"""
import copy
import ctypes
import getpass
import json
import sys
import time
import winreg
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QInputDialog, QMenu, QSystemTrayIcon

from . import alerts, data, live, sim
from .alerts import ALERTS
from .widgets import KINDS, bg, replay_years

CONFIG = data.APP_DIR / 'config.json'
INSTANCE = f'boxbox-tray-{getpass.getuser()}'  # one app per Windows user, so one live connection
DASH_SETTINGS = data.APP_DIR / 'dashboard.json'  # the dashboard window's layout (it once held its own settings too)
REFIT = data.APP_DIR / 'refit.json'  # sim refits after each race: {'seen': [race labels offered], 'history': [...]}
RUN_KEY, RUN_VALUE = r'Software\Microsoft\Windows\CurrentVersion\Run', 'Box Box'
OLD_RUN_VALUE = 'F1 tracker'  # the app's name until 2026-10-11
NAMES = {'drivers': 'Driver standings', 'teams': 'Constructor standings', 'next': 'Next race',
         'map': 'Track map', 'tower': 'Timing tower', 'sim': 'Strategy sim', 'battles': 'Battles'}
DEFAULTS = {
    'widgets': {k: {'on': k in ('drivers', 'teams', 'next'), 'n': 22 if k == 'tower' else 10, 'geom': None} for k in KINDS},
    'text_opacity': 1.0, 'bg_opacity': 0.85, 'locked': False, 'on_top': False, 'refresh_min': 30, 'live_auth': True,
    'model': {},  # overrides for sim.MODEL, e.g. {"fuel_start_kg": 70, "pass_threshold": 1.0}
    'label_scale': 1.0, 'delay': 0, 'replay_speed': 10,  # driver names on the map, live data delay (s), replay speed
    'my_drivers': [],  # [{'code': 'HAM', 'alerts': True}]: highlighted on the map and tower, battle cards, alerts
    'my_team': {'name': None, 'alerts': False},
    'battles': [],     # pinned battle cards [['HAM', 'LEC']]
    'alerts': {k: True for k in ALERTS},  # alert types on/off
}


def load_config():
    cfg = copy.deepcopy(DEFAULTS)
    try:
        saved = json.loads(CONFIG.read_text())
        for k, v in saved.items():
            if k == 'widgets':
                for w, wc in v.items():
                    cfg['widgets'].setdefault(w, {}).update(wc)
            else:
                cfg[k] = v
        if 'opacity' in saved and 'text_opacity' not in saved:  # older single setting faded text + background together
            cfg['text_opacity'], cfg['bg_opacity'] = saved['opacity'], round(saved['opacity'] * 225 / 255, 2)
    except (OSError, ValueError):
        saved = {}
    if 'delay' not in saved:  # the dashboard kept these in its own file before the two apps became one
        try:
            old = json.loads(DASH_SETTINGS.read_text())
        except (OSError, ValueError):
            old = {}
        cfg.update({k: old[k] for k in ('label_scale', 'delay', 'replay_speed') if k in old})
        if old.get('follow') and not saved.get('my_drivers'):  # its "names for" list: the drivers being followed
            cfg['my_drivers'] = [{'code': c, 'alerts': False} for c in old['follow']]
    cfg.pop('opacity', None)
    cfg.pop('follow', None)
    return cfg


def icon_pixmap(size=64, live_dot=False):
    """The red F1 badge; also saved as boxbox/f1.ico for the desktop shortcut (scripts/create_shortcuts.ps1).
    live_dot: a green dot in the corner (tray, while live)."""
    k = size / 64
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor('#e10600'))
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(QRectF(2 * k, 10 * k, 60 * k, 44 * k), 10 * k, 10 * k)
    p.setPen(QColor('white'))
    f = QFont('Segoe UI')
    f.setPixelSize(round(29 * k))
    f.setWeight(QFont.Black)
    p.setFont(f)
    p.drawText(pm.rect(), Qt.AlignCenter, 'F1')
    if live_dot:
        p.setPen(QPen(QColor('#0B0C10'), 3 * k))
        p.setBrush(QColor('#3DDC84'))
        p.drawEllipse(QRectF(40 * k, 36 * k, 22 * k, 22 * k))
    p.end()
    return pm


def icon(live_dot=False):
    return QIcon(icon_pixmap(live_dot=live_dot))


class App:
    def __init__(self, qapp):
        self.qapp, self.cfg = qapp, load_config()
        self.static, self.windows = {}, {}
        self.state = None   # what the widgets and the dashboard show: the live session, or a replay
        self.live = None    # State of the live feed: on by itself around each session (keep_live), or Go live
        self.live_manual = self.live_paused = False  # Go live outside a session / Stop during one
        self.dash = None    # the live dashboard window, while open
        self.fetched = 0
        self.alerts = alerts.Watch(self)
        self.refit = self.new_fit = None  # (process, races added, result file) while a refit runs; its result to apply
        self.tray = QSystemTrayIcon(icon(), qapp)
        self.tray.setToolTip('Box Box')
        self.menu = QMenu()
        self.menu.aboutToShow.connect(self.build_menu)
        self.tray.setContextMenu(self.menu)
        self.tray.show()
        self.save_timer = QTimer(singleShot=True, interval=1000, timeout=self.save)  # widgets moved/resized
        for k in KINDS:
            if self.cfg['widgets'][k]['on']:
                self.show(k)
        self.fetch()
        self.refresh_timer = QTimer(timeout=self.fetch, interval=self.cfg['refresh_min'] * 60000)
        self.refresh_timer.start()
        self.live_timer = QTimer(timeout=self.tick, interval=500)
        self.live_timer.start()
        self.last_fg = None
        self.desk_timer = QTimer(timeout=self.keep_on_desktop, interval=250)
        self.desk_timer.start()
        qapp.aboutToQuit.connect(self.save)
        # a monitor unplugged or rearranged: bring stranded widgets back (Qt only moves them off a removed screen)
        qapp.screenAdded.connect(self.watch_screen)
        qapp.screenRemoved.connect(lambda s: QTimer.singleShot(500, self.fit_screens))
        for s in qapp.screens():
            self.watch_screen(s)

    def watch_screen(self, s):
        s.geometryChanged.connect(lambda _: QTimer.singleShot(500, self.fit_screens))
        QTimer.singleShot(500, self.fit_screens)

    def fit_screens(self):
        for w in self.windows.values():
            w.keep_on_screen()

    def keep_on_desktop(self):
        """Desktop layer that survives Win+D (Rainmeter's 'On desktop' approach), checked every 250 ms: whenever the
        foreground window changes, lift the widgets above the desktop (topmost) if the desktop is in front, otherwise
        put them back at the bottom, behind every app."""
        if self.cfg['on_top']:  # Qt's always-on-top flag does the job
            self.last_fg = None
            return
        fg = user32.GetForegroundWindow()
        hwnds = [int(w.winId()) for w in self.windows.values()]
        if fg == self.last_fg or fg in hwnds:  # clicking/dragging a widget keeps the current layer
            return
        self.last_fg = fg
        up = desktop_in_front(fg)
        for h in hwnds:
            user32.SetWindowPos(h, HWND_TOPMOST if up else HWND_NOTOPMOST, 0, 0, 0, 0, SWP_KEEP)
            if not up:
                user32.SetWindowPos(h, HWND_BOTTOM, 0, 0, 0, 0, SWP_KEEP)

    def model(self):
        """Sim knobs for the session's season (its regulation era's calibration), config overrides on top."""
        year = self.state and self.state.season()
        return sim.season_model(year or max(sim.SEASONS), self.cfg['model'])

    def save(self):
        data.APP_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG.write_text(json.dumps(self.cfg, indent=1))

    def save_soon(self):
        """Positions are saved a second after a move/resize, not only on Quit: a Windows restart doesn't quit cleanly."""
        self.save_timer.start()

    # ---- data -----------------------------------------------------------------------------------------------------
    def fetch(self):
        def work():
            out = {}
            for key, f in (('drivers', data.driver_standings), ('teams', data.constructor_standings), ('next', data.next_race),
                           ('signed_in', live.token_ok)):
                try:
                    out[key] = f()
                except Exception as e:  # offline with no cache: the widget shows the error
                    out[key] = e
            try:
                out['counted'] = data.round_counted(out['next']['round'])
            except Exception as e:  # unknown: the fallback projection stays hidden rather than risk double counting
                out['counted'] = e
            return out

        self.fetched = time.monotonic()

        def done(out):
            self.static.update(out if isinstance(out, dict) else {})
            for w in self.windows.values():
                w.refresh()
        bg(work, done)
        self.check_refit()

    # ---- sim refit after each race -------------------------------------------------------------------------------
    def check_refit(self):
        """A race or sprint of this season reached F1's archive that the sim's fit hasn't seen: refit its era in a
        background process (calibrate.py --json, low priority, a quarter of an hour or so), then offer the result."""
        if self.refit or self.new_fit or self.session_on():
            return
        year = datetime.now(timezone.utc).year

        def work():
            from . import calibrate
            fitted = set(sim.CALIBRATION.get(sim.era(year), {}).get('_fitted_on') or ())
            return [label for label, _ in calibrate.races([year]) if label not in fitted | set(refit_log()['seen'])]

        def done(new):
            if not isinstance(new, list) or not new or self.refit:
                return
            import subprocess
            out = data.APP_DIR / 'refit_result.json'
            out.unlink(missing_ok=True)
            exe = Path(sys.executable).with_name('pythonw.exe')
            log = open(data.APP_DIR / 'refit.log', 'w', encoding='utf-8')
            proc = subprocess.Popen([str(exe if exe.exists() else sys.executable), '-m', 'boxbox.calibrate', str(year),
                                     '--json', str(out)], cwd=Path(__file__).resolve().parents[1], stdout=log, stderr=log,
                                    creationflags=0x4000 | 0x08000000)  # BELOW_NORMAL_PRIORITY_CLASS | CREATE_NO_WINDOW
            self.refit = (proc, new, out, year)
        bg(work, done)

    def poll_refit(self):
        """The refit finished: tell how the sim's backtest moved and offer it (tray → Apply new sim fit)."""
        proc, new, out, year = self.refit
        if proc.poll() is None:
            return
        self.refit = None
        log = refit_log()
        log['seen'] = sorted(set(log['seen']) | set(new))  # offered once, applied or not
        try:
            eras = json.loads(out.read_text())['eras']
        except (OSError, ValueError, KeyError):
            print('refit failed, see refit.log')
            save_refit_log(log)
            return
        era = sim.era(year)
        if era not in eras:
            save_refit_log(log)
            return
        old, fit = sim.CALIBRATION.get(era, {}).get('_scores') or {}, eras[era]
        text = ', '.join(new) + ' added. ' + score_change(old, fit['_scores'])
        log['history'] = log['history'][-30:] + [{'date': datetime.now(timezone.utc).date().isoformat(), 'era': era,
                                                  'added': new, 'races': len(fit['_fitted_on']), 'scores': fit['_scores']}]
        save_refit_log(log)
        self.new_fit = (era, fit, text)
        self.notify('Strategy sim refit ready', text + ' Tray → Apply new sim fit to use it.')

    def apply_refit(self):
        """Use the new fit from now on (written to calibration.json, like calibrate.py --write)."""
        from . import calibrate
        era, fit, _ = self.new_fit
        try:
            saved = json.loads(calibrate.OUT.read_text()).get('eras', {})
        except (OSError, ValueError):
            saved = {}
        saved[era] = fit
        calibrate.OUT.write_text(json.dumps({'eras': saved}, indent=1))
        sim.CALIBRATION[era] = fit
        sim.MODEL.update(sim.season_model(max(sim.SEASONS)))
        self.new_fit = None
        self.tray.showMessage('Strategy sim', f'New fit for {era} in use.')

    def tick(self):
        for st in {id(s): s for s in (self.state, self.live) if s}.values():  # a replay on screen: live keeps logging
            st.pump()
        # results land in Jolpica minutes after the flag; refetch often so a projection never stacks on them
        if self.live and time.monotonic() - self.fetched > 300:
            self.fetch()
        self.keep_live()
        if self.refit:
            self.poll_refit()
        for w in self.windows.values():
            w.tick()
        self.show_status()
        try:
            self.alerts.check()
        except Exception as e:  # an alert bug must not stop the feeds and widgets
            print('alerts:', repr(e))

    def notify(self, title, text):
        """An alert: a popup on the dashboard while it's open on screen, else a Windows notification."""
        if self.dash and self.dash.isVisible() and not self.dash.isMinimized():
            self.dash.popup(title, text)
        else:
            self.tray.showMessage(title, text, QSystemTrayIcon.Information, 10000)

    # ---- feeds ----------------------------------------------------------------------------------------------------
    def session_on(self):
        """From 10 min before a session of the current weekend to 30 min after it (schedule from Jolpica)."""
        race, now = self.static.get('next'), datetime.now(timezone.utc)
        return isinstance(race, dict) and any(data.live_session(race, now + timedelta(minutes=m)) for m in (10, -30))

    def keep_live(self):
        """Live by itself around every session, recorded (LiveFeed.restore picks it up after a restart)."""
        on = self.session_on()
        if not on:
            self.live_paused = False  # Stop holds until that session is over
        if on and not self.live and not self.live_paused:
            self.go_live()
        elif not on and self.live and not self.live_manual:
            self.stop_live()

    def go_live(self, manual=False, keep=False):
        """keep=True reconnects into the same State (after F1 TV sign-in), so laps logged so far stay for the sim."""
        old = self.live
        if old and old.feed:
            old.feed.stop = True
        st = old if keep and old else live.State()
        st.delay = self.cfg['delay']
        live.LiveFeed(st, auth=self.cfg['live_auth'], record=True).start()
        self.live, self.live_manual, self.live_paused = st, self.live_manual or manual, False
        if self.state is None or self.state is old:  # a replay on screen stays there
            self.state = st

    def stop_live(self):
        if self.live and self.live.feed:
            self.live.feed.stop = True
        if self.state is self.live:
            self.state = None
        self.live, self.live_manual = None, False

    def start_replay(self, path, speed=None, gps=True):
        if self.state and self.state is not self.live and self.state.feed:
            self.state.feed.stop = True
        self.state = live.State()
        live.ReplayFeed(self.state, path, float(speed or self.cfg['replay_speed']), gps=gps).start()

    def stop_feed(self):
        """Stop what's on screen: a replay goes back to the live session (if one runs); live stops until the session
        is over (or Go live)."""
        if self.state and self.state is not self.live:
            if self.state.feed:
                self.state.feed.stop = True
            self.state = self.live
        elif self.live:
            self.stop_live()
            self.live_paused = self.session_on()

    def status(self):
        """One line for the tray tooltip and menu: 'LIVE · Race · L35/62 · recording', or the next session."""
        st = self.state
        if st and st is not self.live:
            s = st.session()
            return ' · '.join(x for x in ('REPLAY', s['name'], f"L{s['lap']}/{s['total']}" if s['total'] else '',
                                          s['track']) if x) + (' · live recording' if self.live else '')
        if self.live:
            s = self.live.session()
            return ' · '.join(x for x in ('LIVE', s['name'] or 'waiting for session data', f"L{s['lap']}/{s['total']}"
                                          if s['total'] else '', 'recording') if x)
        race, now = self.static.get('next'), datetime.now(timezone.utc)
        nxt = next((x for x in race['sessions'] if x[1] > now), None) if isinstance(race, dict) else None
        if not nxt:
            return 'no session'
        left = int((nxt[1] - now).total_seconds())
        return f"next: {nxt[0]} in {left // 86400}d {left % 86400 // 3600:02}:{left % 3600 // 60:02}"

    def show_status(self):
        tip, dot = 'Box Box\n' + self.status(), bool(self.live)
        if tip != self.tray.toolTip():
            self.tray.setToolTip(tip)
        if dot != getattr(self, 'dot', None):
            self.dot = dot
            self.tray.setIcon(icon(dot))

    def open_dashboard(self):
        from .dashboard import Dashboard  # here: dashboard imports this module
        if not self.dash:
            self.dash = Dashboard(self)
            self.dash.show()
        self.dash.bring_to_front()

    # ---- windows --------------------------------------------------------------------------------------------------
    def show(self, k):
        self.cfg['widgets'][k]['on'] = True
        if k not in self.windows:
            self.windows[k] = KINDS[k](self, k)
            self.windows[k].apply_opacity()  # again: subclasses add children after Widget.__init__
            self.windows[k].refresh()
            self.windows[k].tick()
        self.windows[k].show()
        self.last_fg = None  # re-layer on the next check

    def toggle(self, k, on):
        if on:
            self.show(k)
        else:
            self.cfg['widgets'][k]['on'] = False
            w = self.windows.pop(k, None)
            if w:
                w.close()
                w.deleteLater()
        self.save()

    def set_n(self, k, n):
        self.cfg['widgets'][k]['n'] = n
        if k in self.windows:
            self.windows[k].refresh()
            self.windows[k].tick()
        self.save()

    def set(self, key, value):
        self.cfg[key] = value
        if key == 'on_top':
            for w in self.windows.values():
                w.apply_flags()
                w.show()
            self.last_fg = None
        if key in ('text_opacity', 'bg_opacity'):
            for w in self.windows.values():
                w.apply_opacity()
        if key == 'refresh_min':
            self.refresh_timer.setInterval(value * 60000)
        if key == 'delay' and self.live:
            self.live.delay = value
        if self.dash:
            self.dash.map.update()
        self.save()

    def another_launch(self, msg=b''):
        """The app was started again while it runs (the desktop shortcut): open the dashboard. Windows' login start
        (--tray) only brings the widgets back."""
        for w in self.windows.values():
            w.show()
        if msg != b'tray':
            self.open_dashboard()

    def open_settings(self, parent=None):
        from .dashboard import Settings
        if not getattr(self, 'settings', None):
            self.settings = Settings(self)
        self.settings.fill()
        self.settings.show()
        self.settings.raise_()
        self.settings.activateWindow()

    def sign_in(self, done=None):
        """F1 TV sign-in in the browser; live reconnects with the token (real GPS) into the same session."""
        self.tray.showMessage('F1 TV', 'Sign in in your browser. ' + live.SIGNIN_HELP)

        def finished(ok):
            self.static['signed_in'] = ok is True
            if ok is True and self.live:
                self.go_live(keep=True)
            self.tray.showMessage('F1 TV', 'Signed in.' if ok is True else f'Sign-in did not finish: {ok}')
            if done:
                done(ok)
        bg(live.sign_in, finished)

    # ---- tray menu (rebuilt on every open so checkmarks are always current) ---------------------------------------
    def build_menu(self):
        m = self.menu
        m.clear()

        def act(menu, text, fn, checked=None):
            a = QAction(text, menu)
            if checked is not None:
                a.setCheckable(True)
                a.setChecked(checked)
            a.triggered.connect(fn)
            menu.addAction(a)
            return a

        feed = self.state.feed if self.state else None
        m.addAction(self.status() + (f' ({self.live.feed.status})' if self.live and self.live.feed else '')).setEnabled(False)
        act(m, 'Open live dashboard (map + timing + sim)', self.open_dashboard)
        if not self.live:
            act(m, 'Go live now (goes live by itself around every session)', lambda: self.go_live(manual=True))
        act(m, 'Settings… (my drivers, alerts, delay)', self.open_settings)
        act(m, 'Sign in to F1 TV…' + (' (signed in)' if self.static.get('signed_in') is True else ''), self.sign_in)
        act(m, 'Use F1 TV auth for live', lambda c: self.set('live_auth', c), self.cfg['live_auth'])

        rp = m.addMenu('Replay a past session')
        gps = rp.addMenu('…without GPS (test estimated map)')
        replay_years(rp, lambda p: self.tray_replay(p))
        replay_years(gps, lambda p: self.tray_replay(p, gps=False))
        if isinstance(feed, live.ReplayFeed):
            sp = m.addMenu('Replay speed')
            for s in (1, 5, 10, 30, 60):
                act(sp, f'{s}x', lambda _=0, s=s: feed.set_speed(float(s)), feed.speed == s)
            act(sp, 'Pause', lambda _=0: feed.set_speed(0), feed.speed == 0)
            if len(feed.lap_starts) > 1:
                act(m, 'Jump to lap…', lambda _=0: self.jump_to_lap(feed))
        if feed:
            act(m, 'Stop replay (back to live)' if self.state is not self.live and self.live else
                'Stop replay' if self.state is not self.live else 'Stop live until this session is over', self.stop_feed)
        m.addSeparator()

        for k, name in NAMES.items():
            act(m, name, lambda c, k=k: self.toggle(k, c), k in self.windows)
        nm = m.addMenu('Rows shown')
        for k in ('drivers', 'teams', 'tower'):
            sub = nm.addMenu(NAMES[k])
            for n in (3, 5, 10, 15, 22):
                act(sub, f'Top {n}', lambda _=0, k=k, n=n: self.set_n(k, n), self.cfg['widgets'][k]['n'] == n)
        for key, title, values in (('text_opacity', 'Text opacity', (1.0, 0.85, 0.7, 0.55, 0.4)),
                                   ('bg_opacity', 'Background opacity', (1.0, 0.85, 0.7, 0.5, 0.3, 0.15, 0.0))):
            op = m.addMenu(title)
            for o in values:
                act(op, f'{o:.0%}', lambda _=0, k=key, o=o: self.set(k, o), abs(self.cfg[key] - o) < 1e-3)
        rf = m.addMenu('Refresh standings')
        for mins in (5, 15, 30, 60):
            act(rf, f'every {mins} min', lambda _=0, x=mins: self.set('refresh_min', x), self.cfg['refresh_min'] == mins)
        act(rf, 'now', self.fetch)
        act(m, 'Lock positions', lambda c: self.set('locked', c), self.cfg['locked'])
        act(m, 'Always on top (off = desktop layer)', lambda c: self.set('on_top', c), self.cfg['on_top'])
        act(m, 'Start with Windows', set_startup, startup_command() == startup_registered())
        act(m, 'Edit sim model knobs (config.json)', self.open_config)
        if self.new_fit:
            act(m, f'Apply new sim fit: {self.new_fit[2]}', self.apply_refit)
        hist = m.addMenu('Strategy sim accuracy' + (' (refitting…)' if self.refit else ''))
        for h in reversed(refit_log()['history'][-10:]):
            hist.addAction(f"{h['date']} · {h['era']} · {h['races']} races (+{', '.join(h['added'])}) · "
                           + score_change({}, h['scores'])).setEnabled(False)
        if hist.isEmpty():
            hist.addAction('Refits after each race show here').setEnabled(False)
        m.addSeparator()
        act(m, 'Quit', self.qapp.quit)

    def jump_to_lap(self, feed):
        s = self.state.session()
        lap, ok = QInputDialog.getInt(None, 'Jump to lap', f"Lap (1–{s['total'] or max(feed.lap_starts)}):",
                                      max(s['lap'], 1), 1, s['total'] or max(feed.lap_starts))
        if ok:
            feed.seek_lap(lap)

    def tray_replay(self, path, gps=True):
        self.start_replay(path, 10.0, gps)
        for k in ('map', 'tower'):
            self.show(k)

    def open_config(self):
        import os
        self.save()
        os.startfile(CONFIG)  # ponytail: hand-edit JSON, restart to apply; a settings dialog when knobs get used a lot


def refit_log():
    try:
        return {'seen': [], 'history': [], **json.loads(REFIT.read_text())}
    except (OSError, ValueError):
        return {'seen': [], 'history': []}


def save_refit_log(log):
    data.APP_DIR.mkdir(parents=True, exist_ok=True)
    REFIT.write_text(json.dumps(log, indent=1))


def score_change(old, new):
    """'position error 1.23 -> 1.21, real finish in the 80 % range 80 % -> 81 %, lap time error 0.69 -> 0.68 s'
    (leave-one-race-out backtest means; without old ones just the new)."""
    parts = []
    for k, label, fmt in (('pos_err', 'position error', '{:.2f}'), ('cover80', 'real finish in the 80 % range', '{:.0%}'),
                          ('mae', 'lap time error', '{:.2f} s')):
        if k in new:
            parts.append(f'{label} ' + (f'{fmt.format(old[k])} → ' if k in old else '') + fmt.format(new[k]))
    return ', '.join(parts)


def startup_command():
    """What Windows runs at login: the frozen exe, or this venv's pythonw (no console window) on run.pyw. --tray: the
    widgets only (the desktop shortcut also opens the dashboard)."""
    if getattr(sys, 'frozen', False):
        return f'"{sys.executable}" --tray'
    return f'"{Path(sys.executable).with_name("pythonw.exe")}" "{Path(__file__).resolve().parents[1] / "run.pyw"}" --tray'


def startup_registered(name=None):
    """The command registered in HKCU Run (per user, no admin needed), or None."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            return winreg.QueryValueEx(k, name or RUN_VALUE)[0]
    except OSError:
        return None


def set_startup(on, name=None):
    name = name or RUN_VALUE
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if on:
            winreg.SetValueEx(k, name, 0, winreg.REG_SZ, startup_command())
        elif startup_registered(name) is not None:
            winreg.DeleteValue(k, name)


# ---- Windows: desktop detection, single instance ---------------------------------------------------------------------
user32 = ctypes.windll.user32
kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
user32.GetForegroundWindow.restype = user32.GetShellWindow.restype = user32.FindWindowExW.restype = ctypes.c_void_p
user32.FindWindowExW.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_wchar_p]
user32.GetClassNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
user32.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p] + [ctypes.c_int] * 4 + [ctypes.c_uint]
user32.AllowSetForegroundWindow.argtypes = [ctypes.c_uint]
kernel32.CreateMutexW.restype = ctypes.c_void_p
_mutexes = []  # held until the process exits
HWND_TOPMOST, HWND_NOTOPMOST, HWND_BOTTOM = -1, -2, 1
SWP_KEEP = 0x0001 | 0x0002 | 0x0010  # NOSIZE | NOMOVE | NOACTIVATE


def desktop_in_front(fg):
    """Win+D / Show desktop / a click on the wallpaper: the foreground window is the desktop. Windows 11 24H2+: the
    shell window itself; earlier: a WorkerW/Progman window hosting the icons (SHELLDLL_DefView). From Rainmeter."""
    if not fg:
        return False
    if fg == user32.GetShellWindow():
        return True
    cls = ctypes.create_unicode_buffer(32)
    user32.GetClassNameW(fg, cls, 32)
    return cls.value in ('WorkerW', 'Progman') and bool(user32.FindWindowExW(fg, None, 'SHELLDLL_DefView', None))


def already_running(name, msg=b''):
    """True if this Windows user already runs `name` (named mutex: atomic, unlike probing a socket). Pokes the running
    copy with msg (see listen) and lets it take the foreground, so it can show itself; the caller then exits."""
    _mutexes.append(kernel32.CreateMutexW(None, False, f'Local\\{name}'))
    if ctypes.get_last_error() != 183:  # ERROR_ALREADY_EXISTS
        return False
    user32.AllowSetForegroundWindow(0xFFFFFFFF)  # ASFW_ANY: only the process the user launched may grant focus
    s = QLocalSocket()
    s.connectToServer(name)
    if s.waitForConnected(1000):
        s.write(msg or b'-')
        s.waitForBytesWritten(1000)
    return True


def listen(name, on_launch):
    """Calls on_launch(msg) whenever another copy of `name` is started (and exits). Keep the returned server."""
    server = QLocalServer()

    def poked():
        while server.hasPendingConnections():
            c = server.nextPendingConnection()
            c.waitForReadyRead(500)
            msg = bytes(c.readAll())
            c.close()
            on_launch(msg)
    server.newConnection.connect(poked)
    server.listen(name)
    return server


def console_to_file(name):
    """pythonw.exe (shortcuts, autostart) has no console: sys.stdout/stderr are None and code that writes to them
    crashes. Send them to a file in the app folder instead, rewritten at each launch."""
    if sys.stderr is None or sys.stdout is None:
        data.APP_DIR.mkdir(parents=True, exist_ok=True)
        sys.stdout = sys.stderr = open(data.APP_DIR / f'{name}.log', 'w', encoding='utf-8', buffering=1)


def main():
    """Tray, desktop widgets and the live feed; opens the dashboard too unless started with --tray (Windows login)."""
    console_to_file('tray')
    qapp = QApplication(sys.argv)
    tray_only = '--tray' in sys.argv
    if already_running(INSTANCE, b'tray' if tray_only else b'dash'):
        return
    if startup_registered(OLD_RUN_VALUE) is not None:  # registered under the old name: same entry, new name
        set_startup(False, OLD_RUN_VALUE)
        set_startup(True)
    if startup_registered() not in (None, startup_command()):  # registered by an older version (no --tray)
        set_startup(True)
    qapp.setQuitOnLastWindowClosed(False)
    qapp.f1 = App(qapp)  # keep a reference so the tray and widgets aren't garbage-collected
    qapp.f1.server = listen(INSTANCE, qapp.f1.another_launch)
    if not tray_only:
        qapp.f1.open_dashboard()
    sys.exit(qapp.exec())


if __name__ == '__main__':
    main()
