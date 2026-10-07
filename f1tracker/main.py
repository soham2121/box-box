"""Tray app: config, background fetches, feeds, and the widget windows. Run: python -m f1tracker.main"""
import copy
import ctypes
import getpass
import json
import sys
import time
import winreg
from pathlib import Path

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QInputDialog, QMenu, QSystemTrayIcon

from . import data, live, sim
from .widgets import KINDS, bg, replay_years

CONFIG = data.APP_DIR / 'config.json'
INSTANCE = f'f1tracker-tray-{getpass.getuser()}'  # one tray app per Windows user
RUN_KEY, RUN_VALUE = r'Software\Microsoft\Windows\CurrentVersion\Run', 'F1 tracker'
NAMES = {'drivers': 'Driver standings', 'teams': 'Constructor standings', 'next': 'Next race',
         'map': 'Track map', 'tower': 'Timing tower', 'sim': 'Strategy sim'}
DEFAULTS = {
    'widgets': {k: {'on': k in ('drivers', 'teams', 'next'), 'n': 22 if k == 'tower' else 10, 'geom': None} for k in KINDS},
    'text_opacity': 1.0, 'bg_opacity': 0.85, 'locked': False, 'on_top': False, 'refresh_min': 30, 'live_auth': True,
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
        if 'opacity' in saved and 'text_opacity' not in saved:  # older single setting faded text + background together
            cfg['text_opacity'], cfg['bg_opacity'] = saved['opacity'], round(saved['opacity'] * 225 / 255, 2)
    except (OSError, ValueError):
        pass
    cfg.pop('opacity', None)
    return cfg


def icon_pixmap(size=64):
    """The red F1 badge; also saved as f1tracker/f1.ico for the desktop shortcuts (scripts/create_shortcuts.ps1)."""
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
    p.end()
    return pm


def icon():
    return QIcon(icon_pixmap())


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

    def tick(self):
        if self.state:
            self.state.pump()
            # results land in Jolpica minutes after the flag; refetch often so a projection never stacks on them
            if isinstance(self.state.feed, live.LiveFeed) and time.monotonic() - self.fetched > 300:
                self.fetch()
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
        self.save()

    def another_launch(self):
        """Someone started the app again while it runs: point them at the tray instead of opening a second copy."""
        for w in self.windows.values():
            w.show()
        self.tray.showMessage('F1 tracker', 'Already running: right-click the red F1 icon in the tray.')

    def sign_in(self):
        self.tray.showMessage('F1 TV', 'Sign in in your browser. ' + live.SIGNIN_HELP)
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
        act(m, 'Open live dashboard (map + timing + sim)', open_dashboard)
        act(m, 'Live tracker (F1 TV if signed in)', lambda: self.start(live.LiveFeed, auth=self.cfg['live_auth']))
        act(m, 'Sign in to F1 TV…' + (' (signed in)' if self.static.get('signed_in') is True else ''), self.sign_in)
        act(m, 'Use F1 TV auth for live', lambda c: self.set('live_auth', c), self.cfg['live_auth'])

        rp = m.addMenu('Replay a past session')
        gps = rp.addMenu('…without GPS (test estimated map)')
        replay_years(rp, lambda p: self.start(live.ReplayFeed, p, 10.0))
        replay_years(gps, lambda p: self.start(live.ReplayFeed, p, 10.0, gps=False))
        if isinstance(feed, live.ReplayFeed):
            sp = m.addMenu('Replay speed')
            for s in (1, 5, 10, 30, 60):
                act(sp, f'{s}x', lambda _=0, s=s: feed.set_speed(float(s)), feed.speed == s)
            act(sp, 'Pause', lambda _=0: feed.set_speed(0), feed.speed == 0)
            if len(feed.lap_starts) > 1:
                act(m, 'Jump to lap…', lambda _=0: self.jump_to_lap(feed))
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
        m.addSeparator()
        act(m, 'Quit', self.qapp.quit)

    def jump_to_lap(self, feed):
        s = self.state.session()
        lap, ok = QInputDialog.getInt(None, 'Jump to lap', f"Lap (1–{s['total'] or max(feed.lap_starts)}):",
                                      max(s['lap'], 1), 1, s['total'] or max(feed.lap_starts))
        if ok:
            feed.seek_lap(lap)

    def stop_feed(self):
        if self.state and self.state.feed:
            self.state.feed.stop = True
        self.state = None

    def open_config(self):
        import os
        self.save()
        os.startfile(CONFIG)  # ponytail: hand-edit JSON, restart to apply; a settings dialog when knobs get used a lot


def startup_command():
    """What Windows runs at login: the frozen exe, or this venv's pythonw (no console window) on run.pyw."""
    if getattr(sys, 'frozen', False):
        return f'"{sys.executable}"'
    return f'"{Path(sys.executable).with_name("pythonw.exe")}" "{Path(__file__).resolve().parents[1] / "run.pyw"}"'


def startup_registered():
    """The command registered in HKCU Run (per user, no admin needed), or None."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            return winreg.QueryValueEx(k, RUN_VALUE)[0]
    except OSError:
        return None


def set_startup(on):
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if on:
            winreg.SetValueEx(k, RUN_VALUE, 0, winreg.REG_SZ, startup_command())
        elif startup_registered() is not None:
            winreg.DeleteValue(k, RUN_VALUE)


def open_dashboard():
    """The dashboard is its own process (own window, own feed); same interpreter, no console window."""
    import subprocess
    exe = Path(sys.executable).with_name('pythonw.exe')
    subprocess.Popen([str(exe if exe.exists() else sys.executable), str(Path(__file__).resolve().parents[1] / 'run_live.pyw')])


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


def already_running(name):
    """True if this Windows user already runs `name` (named mutex: atomic, unlike probing a socket). Pokes the running
    copy (see listen) and lets it take the foreground, so it can show itself; the caller then exits."""
    _mutexes.append(kernel32.CreateMutexW(None, False, f'Local\\{name}'))
    if ctypes.get_last_error() != 183:  # ERROR_ALREADY_EXISTS
        return False
    user32.AllowSetForegroundWindow(0xFFFFFFFF)  # ASFW_ANY: only the process the user launched may grant focus
    s = QLocalSocket()
    s.connectToServer(name)
    s.waitForConnected(1000)
    return True


def listen(name, on_launch):
    """Calls on_launch() whenever another copy of `name` is started (and exits). Keep the returned server."""
    server = QLocalServer()

    def poked():
        while server.hasPendingConnections():
            server.nextPendingConnection().close()
        on_launch()
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
    console_to_file('tray')
    qapp = QApplication(sys.argv)
    if already_running(INSTANCE):
        return
    qapp.setQuitOnLastWindowClosed(False)
    qapp.f1 = App(qapp)  # keep a reference so the tray and widgets aren't garbage-collected
    qapp.f1.server = listen(INSTANCE, qapp.f1.another_launch)
    sys.exit(qapp.exec())


if __name__ == '__main__':
    main()
