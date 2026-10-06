"""Tray app: config, background fetches, feeds, and the widget windows. Run: python -m f1tracker.main"""
import copy
import json
import sys

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from . import data, live, sim
from .widgets import KINDS, bg

CONFIG = data.APP_DIR / 'config.json'
NAMES = {'drivers': 'Driver standings', 'teams': 'Constructor standings', 'next': 'Next race',
         'map': 'Track map', 'tower': 'Timing tower', 'sim': 'Strategy sim'}
DEFAULTS = {
    'widgets': {k: {'on': k in ('drivers', 'teams', 'next'), 'n': 22 if k == 'tower' else 10, 'geom': None} for k in KINDS},
    'opacity': 0.92, 'locked': False, 'on_top': False, 'refresh_min': 30, 'live_auth': True,
    'model': {},  # overrides for sim.MODEL, e.g. {"fuel_start_kg": 70, "pass_threshold": 1.0}
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
    except (OSError, ValueError):
        pass
    return cfg


def icon():
    pm = QPixmap(64, 64)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor('#e10600'))
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(2, 10, 60, 44, 10, 10)
    p.setPen(QColor('white'))
    p.setFont(QFont('Segoe UI', 22, QFont.Black))
    p.drawText(pm.rect(), Qt.AlignCenter, 'F1')
    p.end()
    return QIcon(pm)


class App:
    def __init__(self, qapp):
        self.qapp, self.cfg = qapp, load_config()
        self.static, self.state, self.windows = {}, None, {}
        self.tray = QSystemTrayIcon(icon(), qapp)
        self.tray.setToolTip('F1 tracker')
        self.menu = QMenu()
        self.menu.aboutToShow.connect(self.build_menu)
        self.tray.setContextMenu(self.menu)
        self.tray.show()
        for k in KINDS:
            if self.cfg['widgets'][k]['on']:
                self.show(k)
        self.fetch()
        self.refresh_timer = QTimer(timeout=self.fetch, interval=self.cfg['refresh_min'] * 60000)
        self.refresh_timer.start()
        self.live_timer = QTimer(timeout=self.tick, interval=500)
        self.live_timer.start()
        qapp.aboutToQuit.connect(self.save)

    def model(self):
        return {**sim.MODEL, **self.cfg['model']}

    def save(self):
        data.APP_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG.write_text(json.dumps(self.cfg, indent=1))

    # ---- data -----------------------------------------------------------------------------------------------------
    def fetch(self):
        def work():
            out = {}
            for key, f in (('drivers', data.driver_standings), ('teams', data.constructor_standings), ('next', data.next_race),
                           ('sessions', data.past_sessions), ('signed_in', live.token_ok)):
                try:
                    out[key] = f()
                except Exception as e:  # offline with no cache: the widget shows the error
                    out[key] = e
            try:
                out['round'] = int(data.fetch(f'{data.JOLPICA}/current/driverStandings.json?limit=100', 1800)
                                   ['MRData']['StandingsTable'].get('round', 0))
            except Exception:
                out['round'] = 0
            return out

        def done(out):
            self.static.update(out if isinstance(out, dict) else {})
            for w in self.windows.values():
                w.refresh()
        bg(work, done)

    def tick(self):
        if self.state:
            self.state.pump()
        for w in self.windows.values():
            w.tick()

    def start(self, feed_cls, *args, **kw):
        if self.state and self.state.feed:
            self.state.feed.stop = True
        self.state = live.State()
        feed_cls(self.state, *args, **kw).start()
        for k in ('map', 'tower'):
            self.show(k)

    # ---- windows --------------------------------------------------------------------------------------------------
    def show(self, k):
        self.cfg['widgets'][k]['on'] = True
        if k not in self.windows:
            self.windows[k] = KINDS[k](self, k)
            self.windows[k].refresh()
            self.windows[k].tick()
        self.windows[k].show()

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
        if key in ('on_top', 'opacity'):
            for w in self.windows.values():
                w.apply_flags()
                w.show()
        if key == 'refresh_min':
            self.refresh_timer.setInterval(value * 60000)
        self.save()

    def sign_in(self):
        self.tray.showMessage('F1 TV', 'Sign in in your browser…')
        def done(ok):
            self.static['signed_in'] = ok is True
            self.tray.showMessage('F1 TV', 'Signed in. Restart the live tracker to get GPS positions.' if ok is True
                                  else f'Sign-in did not finish: {ok}')
        bg(live.sign_in, done)

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
        status = getattr(feed, 'status', 'no session')
        m.addAction(status).setEnabled(False)
        act(m, 'Live tracker (F1 TV if signed in)', lambda: self.start(live.LiveFeed, auth=self.cfg['live_auth']))
        act(m, 'Sign in to F1 TV…' + (' (signed in)' if self.static.get('signed_in') is True else ''), self.sign_in)
        act(m, 'Use F1 TV auth for live', lambda c: self.set('live_auth', c), self.cfg['live_auth'])

        rp = m.addMenu('Replay a past session')
        gps = rp.addMenu('…without GPS (test estimated map)')
        sessions = self.static.get('sessions')
        if not isinstance(sessions, list):
            rp.addAction(f'archive unavailable: {sessions}' if sessions else 'loading…').setEnabled(False)
        for label, path in sessions if isinstance(sessions, list) else []:
            act(rp, label, lambda _=0, p=path: self.start(live.ReplayFeed, p, 10.0))
            act(gps, label, lambda _=0, p=path: self.start(live.ReplayFeed, p, 10.0, gps=False))
        if isinstance(feed, live.ReplayFeed):
            sp = m.addMenu('Replay speed')
            for s in (1, 5, 10, 30, 60):
                act(sp, f'{s}x', lambda _=0, s=s: feed.set_speed(float(s)), feed.speed == s)
        if feed:
            act(m, 'Stop feed', self.stop_feed)
        m.addSeparator()

        for k, name in NAMES.items():
            act(m, name, lambda c, k=k: self.toggle(k, c), k in self.windows)
        nm = m.addMenu('Rows shown')
        for k in ('drivers', 'teams', 'tower'):
            sub = nm.addMenu(NAMES[k])
            for n in (3, 5, 10, 15, 22):
                act(sub, f'Top {n}', lambda _=0, k=k, n=n: self.set_n(k, n), self.cfg['widgets'][k]['n'] == n)
        op = m.addMenu('Opacity')
        for o in (1.0, 0.92, 0.8, 0.65, 0.5):
            act(op, f'{o:.0%}', lambda _=0, o=o: self.set('opacity', o), abs(self.cfg['opacity'] - o) < 1e-3)
        rf = m.addMenu('Refresh standings')
        for mins in (5, 15, 30, 60):
            act(rf, f'every {mins} min', lambda _=0, x=mins: self.set('refresh_min', x), self.cfg['refresh_min'] == mins)
        act(rf, 'now', self.fetch)
        act(m, 'Lock positions', lambda c: self.set('locked', c), self.cfg['locked'])
        act(m, 'Always on top (off = desktop layer)', lambda c: self.set('on_top', c), self.cfg['on_top'])
        act(m, 'Edit sim model knobs (config.json)', self.open_config)
        m.addSeparator()
        act(m, 'Quit', self.qapp.quit)

    def stop_feed(self):
        if self.state and self.state.feed:
            self.state.feed.stop = True
        self.state = None

    def open_config(self):
        import os
        self.save()
        os.startfile(CONFIG)  # ponytail: hand-edit JSON, restart to apply; a settings dialog when knobs get used a lot


def main():
    qapp = QApplication(sys.argv)
    qapp.setQuitOnLastWindowClosed(False)
    qapp.f1 = App(qapp)  # keep a reference so the tray and widgets aren't garbage-collected
    sys.exit(qapp.exec())


if __name__ == '__main__':
    main()
