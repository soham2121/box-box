"""Live dashboard: one window with a big track map, the timing tower and the strategy sim, no standings.
Connects to the live feed on launch. Run: python -m f1tracker.dashboard (or double-click "F1 Live.bat")."""
import getpass
import json
import sys
import time
from pathlib import Path

from PySide6.QtCore import QByteArray, Qt, QTimer
from PySide6.QtWidgets import (QApplication, QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QListWidget,
                               QListWidgetItem, QMainWindow, QMenu, QPushButton, QSizePolicy, QSlider, QSpinBox,
                               QSplitter, QStackedWidget, QTabWidget, QToolBar, QToolButton, QWidget)

from . import data, live, sim
from .main import already_running, console_to_file, icon, listen, load_config
from .widgets import (COND_BOLD, STATUS_TINT, RaceControl, SectorTower, SimPanel, StintPanel, TimingTower, TrackMap,
                      TyrePanel, bg, replay_years)

INSTANCE = f'f1tracker-live-{getpass.getuser()}'  # one dashboard per Windows user, so one live connection
SETTINGS = data.APP_DIR / 'dashboard.json'  # own file: the tray app rewrites config.json whenever it saves
SPEEDS = (1, 5, 10, 30, 60)
ACCENT = '#E10600'
CHEVRON = (Path(__file__).parent / 'chevron.svg').as_posix()
STYLE = f'''
QMainWindow {{ background: #0B0C10; }}
QSplitter::handle {{ background: #0B0C10; }}
QToolBar {{ background: #101217; border: 0; border-bottom: 1px solid #1E2129; spacing: 4px; padding: 8px 12px; }}
QToolBar::separator {{ background: #252934; width: 1px; margin: 8px 8px; }}
QToolButton {{ color: #C9CCD6; font: 10pt "Segoe UI"; padding: 8px 14px; border-radius: 10px; }}
QToolButton:hover {{ background: #1C1F27; color: #FFFFFF; }}
QToolButton:disabled {{ color: #5D6372; }}
QToolButton#live {{ background: {ACCENT}; color: #FFFFFF; font-weight: 600; }}
QToolButton#live:hover {{ background: #F5281E; }}
QLabel#brand {{ color: #ECEDF1; font: 15pt "{COND_BOLD}"; padding: 0 14px 0 4px; }}
QLabel#status {{ color: #C9CCD6; font: 10pt "Segoe UI"; }}
QLabel#chip {{ background: #16181E; border: 1px solid #252934; border-radius: 13px; padding: 4px 11px;
               color: #C9CCD6; font: 10pt "{COND_BOLD}"; }}
QMenu {{ background: #16181E; color: #ECEDF1; border: 1px solid #252934; font: 10pt "Segoe UI"; padding: 4px; }}
QMenu::item {{ padding: 6px 22px; border-radius: 6px; }}
QMenu::item:selected {{ background: #252934; }}
QMenu::item:disabled {{ color: #5D6372; }}
QTabWidget::pane {{ border: 0; }}
QTabWidget::tab-bar {{ left: 10px; }}
QTabBar {{ background: transparent; qproperty-drawBase: 0; }}
QTabBar::tab {{ background: transparent; color: #7E8494; font: 11pt "{COND_BOLD}"; padding: 9px 2px 7px; margin-right: 18px;
                border: 0; border-bottom: 2px solid transparent; }}
QTabBar::tab:hover {{ color: #C9CCD6; }}
QTabBar::tab:selected {{ color: #ECEDF1; border-bottom: 2px solid {ACCENT}; }}
QComboBox, QSpinBox {{ background: #0E1015; color: #ECEDF1; border: 1px solid #2A2E39; border-radius: 8px; padding: 5px 8px; }}
QComboBox:disabled, QSpinBox:disabled {{ color: #5D6372; border-color: #1E2129; }}
QComboBox::drop-down {{ border: 0; width: 22px; }}
QComboBox::down-arrow {{ image: url({CHEVRON}); width: 12px; height: 12px; }}
QSpinBox::up-button, QSpinBox::down-button {{ width: 0; border: 0; }}  /* styled boxes lose their arrows: type or scroll */
QComboBox QAbstractItemView {{ background: #16181E; color: #ECEDF1; selection-background-color: #252934; border: 1px solid #252934; }}
QPushButton {{ background: #171920; color: #ECEDF1; border: 1px solid #2E3240; border-radius: 9px; padding: 8px 14px; }}
QPushButton:hover {{ background: #1F222B; }}
QPushButton#primary {{ background: {ACCENT}; border: 0; color: #FFFFFF; font-weight: 600; }}
QPushButton#primary:hover {{ background: #F5281E; }}
QSlider::groove:horizontal {{ height: 4px; background: #252934; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: #ECEDF1; width: 14px; margin: -5px 0; border-radius: 7px; }}
QDialog, QListWidget {{ background: #121419; color: #ECEDF1; }}
QDialog QLabel {{ color: #C9CCD6; }}
QListWidget {{ border: 1px solid #252934; border-radius: 8px; }}
'''
VIEW = {'label_scale': 1.0, 'follow': None, 'delay': 0, 'replay_speed': 10}  # Settings… (saved in dashboard.json); follow None = everyone


class Settings(QDialog):
    """Applies every change at once, no OK button needed."""

    def __init__(self, dash):
        super().__init__(dash, windowTitle='Live settings')
        self.dash = dash
        form = QFormLayout(self)
        size = QSpinBox(minimum=50, maximum=300, singleStep=10, suffix=' %', value=round(dash.cfg['label_scale'] * 100))
        size.valueChanged.connect(lambda v: dash.set_view('label_scale', v / 100))
        form.addRow('Driver names on the map', size)
        delay = QSpinBox(minimum=0, maximum=600, suffix=' s', value=dash.cfg['delay'])
        delay.valueChanged.connect(lambda v: dash.set_view('delay', v))
        form.addRow('Delay live data', delay)
        hint = QLabel('Live timing runs ahead of the F1 TV video. Set the delay so overtakes show on the map when you '
                      'see them on the stream (no spoilers). Live only, not replays.', wordWrap=True)
        hint.setStyleSheet('color: #888')
        form.addRow(hint)
        speed = QComboBox()
        speed.addItems([f'{s}x' for s in SPEEDS])
        speed.setCurrentText(f"{dash.cfg['replay_speed']}x")
        speed.currentTextChanged.connect(lambda t: dash.set_view('replay_speed', int(t[:-1])))
        form.addRow('Replays start at', speed)
        self.list = QListWidget()
        self.list.itemChanged.connect(self.picked)
        form.addRow('Show names for', self.list)
        row = QHBoxLayout()
        for text, on in (('All', True), ('None', False)):
            b = QPushButton(text)
            b.clicked.connect(lambda _=0, on=on: self.check_all(on))
            row.addWidget(b)
        form.addRow('', row)
        self.fill()

    def fill(self):
        """Drivers of the session on screen (plus any picked earlier that aren't in it)."""
        st, follow = self.dash.state, self.dash.cfg['follow']
        dl = {n: i for n, i in (st.d.get('DriverList', {}) if st else {}).items() if isinstance(i, dict) and i.get('Tla')}
        rows = sorted((i['Tla'], i.get('FullName', '')) for i in dl.values())
        rows += [(c, '') for c in follow or () if c not in {r[0] for r in rows}]
        self.list.blockSignals(True)
        self.list.clear()
        for code, name in rows:
            it = QListWidgetItem(f'{code}   {name.title()}'.strip())
            it.setData(Qt.UserRole, code)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if follow is None or code in follow else Qt.Unchecked)
            self.list.addItem(it)
        if not rows:
            self.list.addItem('Go live or start a replay to list the drivers')
        self.list.blockSignals(False)

    def items(self):
        return [self.list.item(i) for i in range(self.list.count()) if self.list.item(i).data(Qt.UserRole)]

    def check_all(self, on):
        self.list.blockSignals(True)
        for it in self.items():
            it.setCheckState(Qt.Checked if on else Qt.Unchecked)
        self.list.blockSignals(False)
        self.picked()

    def picked(self, *_):
        items = self.items()
        codes = [it.data(Qt.UserRole) for it in items if it.checkState() == Qt.Checked]
        self.dash.set_view('follow', None if len(codes) == len(items) else codes)  # all ticked: new drivers show too


class Dashboard(QMainWindow):
    def __init__(self):
        super().__init__()
        cfg = load_config()  # read-only here: sim knobs and the F1 TV preference
        self.cfg = {**cfg, **VIEW, 'widgets': {k: {'n': 22 if k == 'tower' else 0}
                                               for k in ('map', 'tower', 'sim', 'stints', 'rc', 'sectors', 'tyres')}}
        self.settings = None
        self.state, self.static, self.signed_in = None, {}, None
        self.setWindowTitle('F1 live')
        self.setWindowIcon(icon())
        self.setStyleSheet(STYLE)

        # right side: one layout for races/sprints, one for practice/qualifying, picked by the session type.
        # Race control appears in both, so there are two of it (a Qt widget has one parent).
        self.map = TrackMap(self, 'map', self)
        self.tower, self.stints = TimingTower(self, 'tower', self), StintPanel(self, 'stints', self)
        self.sim, self.rc = SimPanel(self, 'sim', self), RaceControl(self, 'rc', self)
        self.sectors, self.tyres, self.rc2 = SectorTower(self, 'sectors', self), TyrePanel(self, 'tyres', self), RaceControl(self, 'rc', self)
        self.top_tabs, self.bottom_tabs = QTabWidget(documentMode=True), QTabWidget(documentMode=True)
        self.top_tabs.addTab(self.tower, 'TIMING')
        self.top_tabs.addTab(self.stints, 'TYRE STINTS')
        self.bottom_tabs.addTab(self.sim, 'STRATEGY SIM')
        self.bottom_tabs.addTab(self.rc, 'RACE CONTROL')
        self.race_split = QSplitter(Qt.Vertical)
        self.race_split.addWidget(self.top_tabs)
        self.race_split.addWidget(self.bottom_tabs)
        self.quali_bottom = QSplitter(Qt.Horizontal)
        self.quali_bottom.addWidget(self.tyres)
        self.quali_bottom.addWidget(self.rc2)
        self.quali_split = QSplitter(Qt.Vertical)
        self.quali_split.addWidget(self.sectors)
        self.quali_split.addWidget(self.quali_bottom)
        self.pages = QStackedWidget()
        self.pages.addWidget(self.race_split)
        self.pages.addWidget(self.quali_split)
        self.split = QSplitter(Qt.Horizontal)
        self.split.addWidget(self.map)
        self.split.addWidget(self.pages)
        self.split.setContentsMargins(10, 10, 10, 10)
        for w in (self.split, self.race_split, self.quali_split, self.quali_bottom):
            w.setHandleWidth(10)  # the gaps between the panels
        self.setCentralWidget(self.split)

        bar = QToolBar(movable=False)
        self.addToolBar(bar)
        bar.addWidget(QLabel(f"<span style='color:{ACCENT}'>▰</span> F1 LIVE", objectName='brand'))
        self.live_btn = bar.addAction('● Go live', self.start_live)
        bar.widgetForAction(self.live_btn).setObjectName('live')
        rp = QToolButton(text='Replay', popupMode=QToolButton.InstantPopup)
        self.replay_menu = QMenu(rp)
        self.replay_menu.aboutToShow.connect(self.build_replay_menu)
        rp.setMenu(self.replay_menu)
        bar.addWidget(rp)
        bar.addAction('■ Stop', self.stop_feed)
        bar.addSeparator()
        self.signin = bar.addAction('Sign in to F1 TV…', self.sign_in)
        self.signin.setToolTip(live.SIGNIN_HELP)
        bar.addAction('Settings…', self.open_settings)
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        bar.addWidget(spacer)
        chips = QWidget()
        self.chip_row = QHBoxLayout(chips)
        self.chip_row.setContentsMargins(0, 0, 0, 0)
        self.chip_row.setSpacing(6)
        bar.addWidget(chips)

        # replay controls, second row, only while a replay plays
        self.addToolBarBreak()
        rb = self.replay_bar = QToolBar(movable=False)
        self.addToolBar(rb)
        self.play = rb.addAction('⏸ Pause', self.toggle_pause)
        self.back = rb.addAction('◀ Lap', lambda: self.step(-1))
        self.fwd = rb.addAction('Lap ▶', lambda: self.step(1))
        self.slider = QSlider(Qt.Horizontal, minimum=0, maximum=1000, pageStep=20)
        self.slider.sliderReleased.connect(self.slider_seek)
        self.slider.actionTriggered.connect(lambda a: a != QSlider.SliderMove and QTimer.singleShot(0, self.slider_seek))
        rb.addWidget(self.slider)
        self.where = QLabel(objectName='status')
        rb.addWidget(self.where)
        self.speed_box = QComboBox()
        self.speed_box.addItems([f'{s}x' for s in SPEEDS])
        self.speed_box.textActivated.connect(lambda t: self.replay() and self.replay().set_speed(float(t[:-1])))
        rb.addWidget(self.speed_box)
        rb.hide()
        self.resume_speed, self.last_step = None, None  # speed to go back to after a pause

        self.restore()
        self.timer = QTimer(self, timeout=self.tick, interval=500)
        self.timer.start()
        bg(live.token_ok, self.got_token)
        QTimer.singleShot(0, self.start_live)  # launched during a session: go straight to live

    def model(self):
        """Sim knobs for the session's season (its regulation era's calibration), config overrides on top."""
        year = self.state and self.state.season()
        return sim.season_model(year or max(sim.SEASONS), self.cfg['model'])

    # ---- feeds -----------------------------------------------------------------------------------------------------
    def start_live(self, keep=False):
        """keep=True reconnects into the same State (after F1 TV sign-in), so laps logged so far stay for the sim."""
        old = self.state
        if old and old.feed:
            old.feed.stop = True
        if not (keep and old):
            self.state = live.State()
        self.state.delay = self.cfg['delay']
        live.LiveFeed(self.state, auth=self.cfg.get('live_auth', True)).start()

    def start_replay(self, path):
        self.stop_feed()
        self.state = live.State()
        live.ReplayFeed(self.state, path, float(self.cfg['replay_speed'])).start()

    def stop_feed(self):
        if self.state and self.state.feed:
            self.state.feed.stop = True
        self.state = None

    def bring_to_front(self):
        """Launched again: show this window instead of a second dashboard with a second live connection."""
        self.setWindowState(self.windowState() & ~Qt.WindowMinimized)
        self.show()
        self.raise_()
        self.activateWindow()

    # ---- replay controls -------------------------------------------------------------------------------------------
    def replay(self):
        feed = self.state.feed if self.state else None
        return feed if isinstance(feed, live.ReplayFeed) and feed.end else None

    def toggle_pause(self):
        feed = self.replay()
        if feed and feed.speed:
            self.resume_speed = feed.speed
            feed.set_speed(0)
        elif feed:
            feed.set_speed(self.resume_speed or float(self.cfg['replay_speed']))

    def step(self, n):
        """Races/sprints: one lap back/forward. Other sessions (no laps): one minute."""
        feed = self.replay()
        if not feed:
            return
        if len(feed.lap_starts) > 1:
            # quick repeated clicks add up: count from the last requested lap, the screen lags the jump a moment
            recent = self.last_step and time.monotonic() - self.last_step[1] < 1.5
            lap = max(1, min((self.last_step[0] if recent else self.state.session()['lap']) + n, max(feed.lap_starts)))
            self.last_step = (lap, time.monotonic())
            feed.seek_lap(lap)
        else:
            feed.seek(feed.now() + 60 * n)

    def slider_seek(self):
        feed = self.replay()
        if feed:
            feed.seek(feed.begin + self.slider.value() / 1000 * (feed.end - feed.begin))

    def update_replay_bar(self):
        feed = self.replay()
        self.replay_bar.setVisible(bool(feed))
        if not feed:
            return
        span = max(feed.end - feed.begin, 1)
        if not self.slider.isSliderDown():
            self.slider.setValue(round((feed.now() - feed.begin) / span * 1000))
        laps = len(feed.lap_starts) > 1
        self.back.setText('◀ Lap' if laps else '◀ 1 min')
        self.fwd.setText('Lap ▶' if laps else '1 min ▶')
        self.play.setText('▶ Play' if not feed.speed else '⏸ Pause')
        if feed.speed and self.speed_box.currentText() != f'{feed.speed:g}x':
            self.speed_box.setCurrentText(f'{feed.speed:g}x')
        clock = lambda s: f'{int(s) // 3600}:{int(s) % 3600 // 60:02}:{int(s) % 60:02}'
        s = self.state.session()
        self.where.setText((f"L{s['lap']}/{s['total']} · " if laps and s['total'] else '')
                           + f'{clock(feed.now() - feed.begin)} / {clock(span)}')

    def build_replay_menu(self):
        m = self.replay_menu
        m.clear()
        feed = self.state.feed if self.state else None
        if isinstance(feed, live.ReplayFeed):
            sp = m.addMenu('Speed')
            for s in SPEEDS:
                a = sp.addAction(f'{s}x', lambda _=0, s=s: feed.set_speed(float(s)))
                a.setCheckable(True)
                a.setChecked(feed.speed == s)
            m.addSeparator()
        replay_years(m, self.start_replay)

    # ---- F1 TV ---------------------------------------------------------------------------------------------------
    def got_token(self, ok):
        self.signed_in = ok is True
        self.signin.setText('F1 TV: signed in ✓ (sign in again…)' if self.signed_in else 'Sign in to F1 TV…')

    def sign_in(self):
        self.signin.setEnabled(False)
        self.signin.setText('Finish the sign-in in your browser (FastF1 Companion extension)…')

        def done(ok):
            self.signin.setEnabled(True)
            self.got_token(ok)
            if ok is True and self.state and isinstance(self.state.feed, live.LiveFeed):
                self.start_live(keep=True)  # reconnect with the token: real GPS positions
            elif ok is not True:
                self.signin.setText(f'F1 TV sign-in failed: {ok}. Try again…')
        bg(live.sign_in, done)

    # ---- settings --------------------------------------------------------------------------------------------------
    def open_settings(self):
        if not self.settings:
            self.settings = Settings(self)
        self.settings.fill()
        self.settings.show()
        self.settings.raise_()

    def set_view(self, key, value):
        self.cfg[key] = value
        if key == 'delay' and self.state:
            self.state.delay = value
        self.save()
        self.map.update()

    # ---- UI --------------------------------------------------------------------------------------------------------
    def tick(self):
        if self.state:
            self.state.pump()
        kind = self.state.session()['type'] if self.state else ''
        if kind:  # unknown until SessionInfo arrives: keep the current layout
            self.pages.setCurrentWidget(self.race_split if kind in ('Race', 'Sprint') else self.quali_split)
        for w in (self.map, self.tower, self.stints, self.sim, self.rc, self.sectors, self.tyres, self.rc2):
            if w.isVisible():  # hidden tabs and the other layout catch up when shown (next tick, 0.5 s)
                w.tick()
        self.show_chips()
        self.update_replay_bar()

    def status_text(self):
        st = self.state
        if not st:
            return 'Not connected. ● Go live during a session, or pick a Replay.'
        feed = getattr(st.feed, 'status', '')
        if isinstance(st.feed, live.LiveFeed) and not st.d.get('SessionInfo'):
            return f'{feed} · connected, waiting for session data (F1 only sends data while a session is on)'
        s, w = st.session(), st.d.get('WeatherData', {})
        if st.lag():
            feed += f' · delayed {st.lag()} s'
        parts = [feed, s['name'], f"L{s['lap']}/{s['total']}" if s['total'] else '', s['track'], s['status']]
        if w:
            parts.append(f"air {w.get('AirTemp', '?')}° · track {w.get('TrackTemp', '?')}°"
                         + (' · RAIN' if str(w.get('Rainfall')) == '1' else ''))
        return ' · '.join(p for p in parts if p)

    def show_chips(self):
        """The status line as chips: one per ' · ' part. Feed in green while live, track status in its flag colour."""
        st = self.state
        parts = self.status_text().split(' · ')
        track = st and st.session()['track']
        tint = st and {'2': '#FFD400', **STATUS_TINT}.get(str(st.d.get('TrackStatus', {}).get('Status')))  # 2: yellow
        while self.chip_row.count() < len(parts):
            self.chip_row.addWidget(QLabel(objectName='chip'))
        for i in range(self.chip_row.count()):
            chip = self.chip_row.itemAt(i).widget()
            chip.setVisible(i < len(parts))
            if i >= len(parts):
                continue
            text = parts[i]
            col = (tint or '#3DDC84') if track and text.startswith(track) else '#3DDC84' if i == 0 and text.startswith('live') else None
            chip.setText((f"<span style='color:{col}'>●</span> " if col else '') + text.upper())
            chip.setStyleSheet(f'color: {col}; border-color: {col};' if col and i else '')  # the feed chip only gets the dot

    def restore(self):
        self.resize(1600, 900)
        self.split.setSizes([1000, 600])
        self.race_split.setSizes([540, 360])
        self.quali_split.setSizes([560, 340])
        try:
            saved = json.loads(SETTINGS.read_text())
        except (OSError, ValueError):
            return
        dec = lambda k: QByteArray.fromBase64(str(saved.get(k, '')).encode())
        self.restoreGeometry(dec('geometry'))
        for k, w in self.splitters().items():
            w.restoreState(dec(k))  # missing or from an older layout: keeps the default sizes
        self.top_tabs.setCurrentIndex(int(saved.get('top_tab', 0)))
        self.bottom_tabs.setCurrentIndex(int(saved.get('bottom_tab', 0)))
        self.cfg.update({k: saved[k] for k in VIEW if k in saved})

    def splitters(self):
        return {'split': self.split, 'race_split': self.race_split, 'quali_split': self.quali_split,
                'quali_bottom': self.quali_bottom}

    def save(self):
        data.APP_DIR.mkdir(parents=True, exist_ok=True)
        enc = lambda b: bytes(b.toBase64()).decode()
        SETTINGS.write_text(json.dumps({'geometry': enc(self.saveGeometry()),
                                        **{k: enc(w.saveState()) for k, w in self.splitters().items()},
                                        'top_tab': self.top_tabs.currentIndex(), 'bottom_tab': self.bottom_tabs.currentIndex(),
                                        **{k: self.cfg[k] for k in VIEW}}))

    def closeEvent(self, e):
        self.save()
        self.stop_feed()
        super().closeEvent(e)


def main():
    console_to_file('live')
    qapp = QApplication(sys.argv)
    if already_running(INSTANCE):
        return
    qapp.dash = Dashboard()  # keep a reference
    qapp.dash.server = listen(INSTANCE, qapp.dash.bring_to_front)
    qapp.dash.show()
    sys.exit(qapp.exec())


if __name__ == '__main__':
    main()
