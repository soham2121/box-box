"""Live dashboard: one window of the tray app (main.App) with a big track map, the timing tower and the strategy sim.
It shows the app's live session or replay (App.state); closing it leaves the tray, widgets and live feed running.
Opened from the tray, or by starting the app again (desktop shortcut)."""
import collections
import html
import json
import time
from pathlib import Path

from PySide6.QtCore import QByteArray, Qt, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFormLayout, QHBoxLayout, QHeaderView, QLabel,
                               QMainWindow, QMenu, QPushButton, QSizePolicy, QSlider, QSpinBox, QSplitter,
                               QStackedWidget, QTableWidget, QTableWidgetItem, QTabWidget, QToolBar, QToolButton, QWidget)

from . import auth, live, radio, sim
from .alerts import ALERTS
from .main import DASH_SETTINGS as SETTINGS, icon
from .widgets import (COND_BOLD, STATUS_TINT, BattlePanel, RaceControl, SectorTower, SimPanel, StintPanel, TimingTower, TrackMap,
                      TyrePanel, replay_years)

SPEEDS = (1, 5, 10, 30, 60)
# Settings → Strategy sim: (config 'model' key, 'life.SOFT' = one entry of a table, label, lowest, highest, step)
KNOBS = [('sc_per_race', 'Safety cars per race', 0, 3, 0.05), ('vsc_per_race', 'VSCs per race', 0, 3, 0.05),
         ('red_per_race', 'Red flags per race', 0, 1, 0.01), ('life.SOFT', 'Soft tyre life (laps)', 5, 80, 1),
         ('life.MEDIUM', 'Medium tyre life (laps)', 5, 80, 1), ('life.HARD', 'Hard tyre life (laps)', 5, 80, 1),
         ('pit_loss', 'Pit loss when nothing better is known (s)', 10, 40, 0.5)]
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
QLabel#popup {{ background: #1C1F27; color: #ECEDF1; border: 1px solid {ACCENT}; border-radius: 10px;
                padding: 10px 12px; font: 10pt "Segoe UI"; }}
QDialog QLabel {{ color: #C9CCD6; }}
QListWidget {{ border: 1px solid #252934; border-radius: 8px; }}
'''


class Settings(QDialog):
    """Settings of the whole app (tray → Settings…, or the dashboard's). Applies every change at once, except the
    delay: that waits for Save (typing 45 over 40 would go through 4 s, and dropping the delay lets out held data that
    can't be held back again: spoilers)."""

    def __init__(self, app):
        super().__init__(None, windowTitle='Box Box settings')
        self.app = app
        self.setWindowIcon(icon())
        self.setStyleSheet(STYLE)
        self.resize(460, 720)
        form = QFormLayout(self)
        size = QSpinBox(minimum=50, maximum=300, singleStep=10, suffix=' %', value=round(app.cfg['label_scale'] * 100))
        size.valueChanged.connect(lambda v: app.set('label_scale', v / 100))
        form.addRow('Driver names on the map', size)
        self.delay = QSpinBox(minimum=0, maximum=600, suffix=' s', value=app.cfg['delay'])
        self.save_delay = QPushButton('Save', enabled=False)
        self.delay.valueChanged.connect(lambda v: self.save_delay.setEnabled(v != app.cfg['delay']))
        self.save_delay.clicked.connect(lambda: (app.set('delay', self.delay.value()), self.save_delay.setEnabled(False)))
        row = QHBoxLayout()
        row.addWidget(self.delay, 1)
        row.addWidget(self.save_delay)
        form.addRow('Delay live data', row)
        self.auto = QCheckBox('Go live by itself around every session', checked=app.cfg['auto_live'])
        self.auto.toggled.connect(lambda on: app.set('auto_live', on))
        form.addRow(self.auto)
        hint = QLabel('Live timing runs ahead of the F1 TV video. Set the delay so overtakes show on the map when you '
                      'see them on the stream (no spoilers), or press ⏱ Sync on the dashboard as the lap counter on '
                      'TV changes. Live only, not replays.', wordWrap=True)
        hint.setStyleSheet('color: #888')
        form.addRow(hint)
        speed = QComboBox()
        speed.addItems([f'{s}x' for s in SPEEDS])
        speed.setCurrentText(f"{app.cfg['replay_speed']}x")
        speed.currentTextChanged.connect(lambda t: app.set('replay_speed', int(t[:-1])))
        form.addRow('Replays start at', speed)

        self.drivers = QTableWidget(0, 3)
        self.drivers.setHorizontalHeaderLabels(['Driver', 'Follow', 'Alerts'])
        self.drivers.verticalHeader().hide()
        self.drivers.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.drivers.itemChanged.connect(self.picked)
        form.addRow(QLabel('<b>My drivers</b>: highlighted on the map and in the timing, a battle card each, and '
                           'alerts for the ones ticked under Alerts', wordWrap=True))
        form.addRow(self.drivers)
        self.team, self.team_alerts = QComboBox(), QCheckBox('Team alerts')
        self.team.currentTextChanged.connect(lambda t: self.set_team(name=None if t == '(none)' else t))
        self.team_alerts.toggled.connect(lambda on: self.set_team(alerts=on))
        row = QHBoxLayout()
        row.addWidget(self.team, 1)
        row.addWidget(self.team_alerts)
        form.addRow('My team', row)
        form.addRow(QLabel('<b>Alerts</b> (races and sprints): a popup on the dashboard while it is open, '
                           'else a Windows notification', wordWrap=True))
        self.kinds = {}
        for k, label in ALERTS.items():
            box = self.kinds[k] = QCheckBox(label)
            box.toggled.connect(lambda on, k=k: app.set('alerts', {**app.cfg['alerts'], k: on}))
            form.addRow(box)
        form.addRow(QLabel('<b>Strategy sim</b>: the fitted value for the session\'s regulation era unless you set '
                           'one (lowest = fitted). Used from the next sim run.', wordWrap=True))
        self.knobs = {}
        for key, label, lo, hi, step in KNOBS:
            box = QDoubleSpinBox(minimum=lo - step, maximum=hi, singleStep=step, decimals=0 if step >= 1 else 2)
            box.setSpecialValueText('fitted')
            box.valueChanged.connect(lambda v, key=key, box=box: self.set_knob(key, None if v < box.minimum() + box.singleStep() / 2 else v))
            self.knobs[key] = (box, QLabel(label))
            form.addRow(self.knobs[key][1], box)
        reset = QPushButton('Strategy sim: all back to fitted')
        reset.clicked.connect(lambda: (app.set('model', {}), self.fill()))
        form.addRow(reset)
        self.fill()

    def fill(self):
        """Drivers and teams of the session on screen, else of the standings (plus my drivers not in either)."""
        app, st = self.app, self.app.state
        self.delay.setValue(app.cfg['delay'])  # drops a value typed but never saved
        dl = [i for i in (st.d.get('DriverList', {}) if st else {}).values() if isinstance(i, dict) and i.get('Tla')]
        standings = app.static.get('drivers') if isinstance(app.static.get('drivers'), list) else []
        rows = {i['Tla']: (i.get('FullName', '').title(), i.get('TeamName', '')) for i in dl} or \
            {d['code']: (d['name'], d['team']) for d in standings}
        mine = {d['code']: d for d in app.cfg['my_drivers']}
        rows.update({c: ('', '') for c in mine if c not in rows})
        self.drivers.blockSignals(True)
        self.drivers.setRowCount(len(rows))
        for i, (code, (name, team)) in enumerate(sorted(rows.items(), key=lambda kv: (kv[1][1], kv[0]))):
            label = QTableWidgetItem(f'{code}   {name}'.strip())
            label.setData(Qt.UserRole, code)
            label.setFlags(Qt.ItemIsEnabled)
            self.drivers.setItem(i, 0, label)
            for col, on in ((1, code in mine), (2, mine.get(code, {}).get('alerts', False))):
                box = QTableWidgetItem()
                box.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
                box.setCheckState(Qt.Checked if on else Qt.Unchecked)
                self.drivers.setItem(i, col, box)
        self.drivers.blockSignals(False)
        teams = sorted({t for _, t in rows.values() if t} | {app.cfg['my_team']['name']} - {None})
        for w in (self.team, self.team_alerts, self.auto, *self.kinds.values()):
            w.blockSignals(True)
        self.auto.setChecked(app.cfg['auto_live'])
        self.team.clear()
        self.team.addItems(['(none)'] + teams)
        self.team.setCurrentText(app.cfg['my_team']['name'] or '(none)')
        self.team_alerts.setChecked(app.cfg['my_team']['alerts'])
        for k, box in self.kinds.items():
            box.setChecked(app.cfg['alerts'].get(k, True))
        fitted = sim.season_model((st and st.season()) or max(sim.SEASONS))
        mine = app.cfg['model']
        get = lambda m, key: (m.get('life') or {}).get(key[5:]) if key.startswith('life.') else m.get(key)
        for key, label, *_ in KNOBS:
            box, text = self.knobs[key]
            box.blockSignals(True)
            box.setValue(get(mine, key) if get(mine, key) is not None else box.minimum())
            box.blockSignals(False)
            text.setText(f'{label} (fitted {get(fitted, key):g})')
        for w in (self.team, self.team_alerts, self.auto, *self.kinds.values()):
            w.blockSignals(False)

    def picked(self, item):
        """Alerts ticked on a driver not followed: follows them too. Follow unticked: alerts off too."""
        t = self.drivers
        t.blockSignals(True)
        if item.column() == 2 and item.checkState() == Qt.Checked:
            t.item(item.row(), 1).setCheckState(Qt.Checked)
        if item.column() == 1 and item.checkState() == Qt.Unchecked:
            t.item(item.row(), 2).setCheckState(Qt.Unchecked)
        t.blockSignals(False)
        old = [d['code'] for d in self.app.cfg['my_drivers']]
        rows = {t.item(i, 0).data(Qt.UserRole): i for i in range(t.rowCount())}
        on = lambda code, col: t.item(rows[code], col).checkState() == Qt.Checked
        keep = [c for c in old if c in rows and on(c, 1)] + [c for c in rows if on(c, 1) and c not in old]  # order kept
        self.app.set('my_drivers', [{'code': c, 'alerts': on(c, 2)} for c in keep] +
                     [d for d in self.app.cfg['my_drivers'] if d['code'] not in rows])

    def set_knob(self, key, value):
        """config 'model' override; None = back to the fitted value. A table entry ('life.SOFT') overrides that entry only
        (sim.season_model merges it into the fitted table)."""
        m = dict(self.app.cfg['model'])
        if key.startswith('life.'):
            life = {k: v for k, v in (m.get('life') or {}).items() if k != key[5:]}
            if value is not None:
                life[key[5:]] = int(value)
            m.pop('life', None)
            if life:
                m['life'] = life
        elif value is None:
            m.pop(key, None)
        else:
            m[key] = value
        self.app.set('model', m)

    def set_team(self, **kw):
        self.app.set('my_team', {**self.app.cfg['my_team'], **kw})


class Dashboard(QMainWindow):
    def __init__(self, app):
        super().__init__()
        self.app = app
        # the docked panels' row counts are the dashboard's own; everything else is the app's config
        self.cfg = collections.ChainMap({'widgets': {k: {'n': 22 if k == 'tower' else 0} for k in (
            'map', 'tower', 'sim', 'stints', 'rc', 'sectors', 'tyres', 'battles')}}, app.cfg)
        self.setWindowTitle('Box Box')
        self.setWindowIcon(icon())
        self.setStyleSheet(STYLE)

        # right side: one layout for races/sprints, one for practice/qualifying, picked by the session type.
        # Race control appears in both, so there are two of it (a Qt widget has one parent).
        self.map = TrackMap(self, 'map', self)
        self.tower, self.stints = TimingTower(self, 'tower', self), StintPanel(self, 'stints', self)
        self.sim, self.rc, self.battles = SimPanel(self, 'sim', self), RaceControl(self, 'rc', self), BattlePanel(self, 'battles', self)
        self.sectors, self.tyres, self.rc2 = SectorTower(self, 'sectors', self), TyrePanel(self, 'tyres', self), RaceControl(self, 'rc', self)
        self.top_tabs, self.bottom_tabs = QTabWidget(documentMode=True), QTabWidget(documentMode=True)
        self.top_tabs.addTab(self.tower, 'TIMING')
        self.top_tabs.addTab(self.stints, 'TYRE STINTS')
        self.top_tabs.addTab(self.battles, 'BATTLES')
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
        bar.addWidget(QLabel(f"<span style='color:{ACCENT}'>▰</span> BOX BOX", objectName='brand'))
        self.live_btn = bar.addAction('● Go live', self.go_live)
        bar.widgetForAction(self.live_btn).setObjectName('live')
        rp = QToolButton(text='Replay', popupMode=QToolButton.InstantPopup)
        self.replay_menu = QMenu(rp)
        self.replay_menu.aboutToShow.connect(self.build_replay_menu)
        rp.setMenu(self.replay_menu)
        bar.addWidget(rp)
        bar.addAction('■ Stop', app.stop_feed)
        bar.addSeparator()
        self.signin = bar.addAction('Sign in to F1 TV…', self.sign_in)
        self.signin.setToolTip(auth.SIGNIN_HELP)
        bar.addAction('Settings…', app.open_settings)
        self.sync = bar.addAction('⏱ Sync delay', self.sync_delay)
        self.sync.setToolTip('Press (or S) the moment the lap counter on the F1 TV stream changes: the live data delay '
                             'is set to match the stream')
        QShortcut(QKeySequence('S'), self, self.sync_delay)
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
        self.popups = []  # alerts on screen, newest at the bottom

        self.restore()
        self.timer = QTimer(self, timeout=self.tick, interval=500)
        self.timer.start()
        self.got_token()
        self.tick()

    # the app's live session or replay, standings and sim knobs (the panels read them through here)
    state = property(lambda self: self.app.state)
    static = property(lambda self: self.app.static)

    def model(self):
        return self.app.model()

    # ---- feeds -----------------------------------------------------------------------------------------------------
    def go_live(self):
        """Back to the live session if a replay is on, else connect now (outside the session windows too)."""
        if self.app.live:
            if self.app.state is not self.app.live:
                self.app.stop_feed()
        else:
            self.app.go_live(manual=True)

    def start_replay(self, path):
        self.app.start_replay(path)

    def sync_delay(self):
        st = self.app.live
        d = st and st.sync_delay(time.time(), self.app.cfg['delay'])
        if d is None:
            self.popup('Sync delay', 'Live races and sprints only, once the lap counter has changed live.')
            return
        self.app.set('delay', d)
        self.popup('Sync delay', f'Live data now runs {d} s behind, matching the stream.')

    def popup(self, title, text):
        """An alert in the top-right corner for 12 s; several stack."""
        note = QLabel(f'<b>{html.escape(title)}</b><br>{html.escape(text)}', self, wordWrap=True, objectName='popup')
        note.setFixedWidth(380)
        note.adjustSize()
        self.popups.append(note)
        note.show()
        self.place_popups()

        def gone():
            if note in self.popups:
                self.popups.remove(note)
                note.deleteLater()
                self.place_popups()
        QTimer.singleShot(12000, gone)

    def place_popups(self):
        y = self.centralWidget().y() + 14
        for n in self.popups:
            n.move(self.width() - n.width() - 24, y)
            n.raise_()
            y += n.height() + 8

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.place_popups()

    def bring_to_front(self):
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
    def got_token(self):
        token, text, _ = self.app.static.get('signed_in') or (None, '', None)
        self.signin.setText(f'F1 TV: {text} ✓ (sign in again…)' if token else 'Sign in to F1 TV…')
        self.signin.setToolTip((text + '. ' if text and not token else '') + auth.SIGNIN_HELP)

    def sign_in(self):
        self.signin.setEnabled(False)
        self.signin.setText('Finish the sign-in in your browser (FastF1 Companion extension)…')

        def done(ok):
            self.signin.setEnabled(True)
            self.got_token()
            if ok is not True:
                self.signin.setText(f'F1 TV sign-in failed: {ok}. Try again…')
        self.app.sign_in(done)

    # ---- UI --------------------------------------------------------------------------------------------------------
    def tick(self):
        """Every 0.5 s (the app pumps the feeds)."""
        kind = self.state.session()['type'] if self.state else ''
        if kind:  # unknown until SessionInfo arrives: keep the current layout
            self.pages.setCurrentWidget(self.race_split if kind in ('Race', 'Sprint') else self.quali_split)
        for w in (self.map, self.tower, self.stints, self.battles, self.sim, self.rc, self.sectors, self.tyres, self.rc2):
            if w.isVisible():  # hidden tabs and the other layout catch up when shown (next tick, 0.5 s)
                w.tick()
        on_live = self.app.live and self.state is self.app.live
        self.sync.setVisible(bool(on_live) and self.state.session()['type'] in ('Race', 'Sprint'))
        self.live_btn.setText('● Back to live' if self.app.live else '● Go live')
        self.live_btn.setVisible(not on_live)
        if self.signin.isEnabled():  # not while a sign-in waits for the browser
            self.got_token()  # the app rechecks the token with every standings refresh
        self.show_chips()
        self.update_replay_bar()

    def status_text(self):
        st = self.state
        if not st:
            return 'Not live. Goes live by itself around every session, or ● Go live / pick a Replay.'
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
        if self.app.live and st is not self.app.live:
            parts.append('live recording')
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

    def splitters(self):
        return {'split': self.split, 'race_split': self.race_split, 'quali_split': self.quali_split,
                'quali_bottom': self.quali_bottom}

    def save(self):
        SETTINGS.parent.mkdir(parents=True, exist_ok=True)
        enc = lambda b: bytes(b.toBase64()).decode()
        SETTINGS.write_text(json.dumps({'geometry': enc(self.saveGeometry()),
                                        **{k: enc(w.saveState()) for k, w in self.splitters().items()},
                                        'top_tab': self.top_tabs.currentIndex(), 'bottom_tab': self.bottom_tabs.currentIndex()}))

    def closeEvent(self, e):
        """Only the window closes: the tray, widgets and live feed carry on. The radio model's memory goes back."""
        self.save()
        self.timer.stop()
        radio.release()
        self.app.dash = None
        self.deleteLater()
        super().closeEvent(e)
