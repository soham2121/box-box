"""Desktop widgets: frameless, draggable, translucent windows. Data comes from data.py (static) and App.state (live)."""
import collections
import html
import threading
import time
from datetime import datetime, timezone

import numpy as np
from PySide6.QtCore import QObject, QPointF, QRectF, QSizeF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import (QApplication, QComboBox, QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QMenu, QPushButton, QScrollArea,
                               QSizeGrip, QSizePolicy, QSpinBox, QVBoxLayout, QWidget)

from . import data, live, radio, sim

TYRE = {'SOFT': ('S', '#ff3333'), 'MEDIUM': ('M', '#ffd400'), 'HARD': ('H', '#f0f0f0'),
        'INTERMEDIATE': ('I', '#39b54a'), 'WET': ('W', '#2f7cff')}
# Pirelli's 2018 range: seven dry compounds, and soft/medium/hard had other colours than from 2019 on
TYRE_2018 = {**TYRE, 'HYPERSOFT': ('HS', '#ff8fd0'), 'ULTRASOFT': ('US', '#a24ad6'), 'SUPERSOFT': ('SS', '#ff3333'),
             'SOFT': ('S', '#ffd400'), 'MEDIUM': ('M', '#f0f0f0'), 'HARD': ('H', '#7fd3ff'), 'SUPERHARD': ('SH', '#ff8c1a')}


def tyre_style(cmp, st):
    """(letter, colour) of a compound in the season being shown."""
    season = TYRE_2018 if st and st.d.get('SessionInfo', {}).get('StartDate', '').startswith('2018') else TYRE
    return season.get(cmp, ('?', '#888'))
POINTS = {'Race': [25, 18, 15, 12, 10, 8, 6, 4, 2, 1], 'Sprint': [8, 7, 6, 5, 4, 3, 2, 1]}
STATUS_TINT = {'4': '#ffd400', '5': '#ff2020', '6': '#ff9900', '7': '#ff9900'}  # SC, red flag, VSC, VSC ending
TARMAC = {'4': '#b89a00', '6': '#b89a00', '7': '#b89a00', '5': '#b01818'}  # SC / VSC yellow, red flag red (dimmed: dots on top)
BLINK = {'6', '7'}  # VSC: the border flashes; SC and red flag stay solid
COND, COND_BOLD = 'Bahnschrift SemiCondensed', 'Bahnschrift SemiBold SemiCondensed'  # live dashboard type


class _Bus(QObject):
    call = Signal(object)


BUS = _Bus()
BUS.call.connect(lambda f: f())  # lives on the UI thread, so emitted callables run there


def bg(work, done):
    """Run work() on a thread, then done(result or exception) on the UI thread."""
    def run():
        try:
            r = work()
        except Exception as e:  # shown in the widget instead of killing the thread silently
            r = e
        BUS.call.emit(lambda: done(r))
    threading.Thread(target=run, daemon=True).start()


REPLAYS = {}  # year -> data.replay_meetings() result, or None while it loads


def replay_years(menu, start):
    """Adds year submenus -> weekend submenus -> sessions to `menu`; start(path) plays one. A year loads when first opened."""
    for year in range(datetime.now(timezone.utc).year, data.FIRST_ARCHIVE_YEAR - 1, -1):
        sub = menu.addMenu(str(year))
        sub.aboutToShow.connect(lambda sub=sub, year=year: fill_year(sub, year, start))


def fill_year(sub, year, start):
    def loaded(r):
        REPLAYS[year] = r
        try:
            if sub.isVisible():  # still open: swap 'loading…' for the weekends
                fill_year(sub, year, start)
        except RuntimeError:  # the menu was rebuilt meanwhile
            pass
    if year not in REPLAYS:
        REPLAYS[year] = None
        bg(lambda: data.replay_meetings(year), loaded)
    r = REPLAYS[year]
    sub.clear()
    if r is None or isinstance(r, Exception) or not r:
        sub.addAction('loading…' if r is None else f'archive unavailable: {r}' if r else 'none in F1\'s archive').setEnabled(False)
        if isinstance(r, Exception):
            del REPLAYS[year]  # try again next time
        return
    for meeting, sessions in r:
        m = sub.addMenu(meeting)
        for session, path in sessions:
            m.addAction(session, lambda _=0, p=path: start(p))


def table(rows, zebra=False, hi=()):
    """Rows of cells -> rich-text table. A cell starting with '#' is a colour bar. zebra: every other row shaded
    (the live dashboard's solid panels; the translucent desktop widgets stay plain). hi: indexes of rows to highlight
    (my drivers)."""
    out = [f'<table cellspacing=0 cellpadding=2 width=100%>']
    for i, r in enumerate(rows):
        out.append(('<tr bgcolor="#2B2F3D">' if i in hi else '<tr bgcolor="#171920">' if zebra and i % 2 else '<tr>') + ''.join(f'<td bgcolor="{c}" width=3></td>' if str(c).startswith('#') else f'<td>{c}</td>'
                                    for c in r) + '</tr>')
    return ''.join(out) + '</table>'


def r(s):
    return f'<div align=right>{s}</div>'


def my_codes(app):
    """My drivers' codes (Settings), in the order picked."""
    return [d['code'] for d in app.cfg.get('my_drivers') or ()]


def team_codes(app, st):
    """Codes of my team's cars in the session on screen: by the session's team name, else the standings' team."""
    name = (app.cfg.get('my_team') or {}).get('name')
    if not name or not st:
        return set()
    dl = [i for i in st.d.get('DriverList', {}).values() if isinstance(i, dict)]
    codes = {i.get('Tla') for i in dl if i.get('TeamName') == name}
    standings = app.static.get('drivers') if isinstance(app.static.get('drivers'), list) else []
    return codes or {d['code'] for d in standings if d['team'] == name}


def scrolled(label):
    """label in a scroll area with a thin scrollbar, for text that outgrows its panel's bottom."""
    scroll = QScrollArea(widgetResizable=True, frameShape=QFrame.NoFrame)
    box = QWidget()  # a layout around the label, so the scroll area hears when the text (and its height) changes
    inner = QVBoxLayout(box)
    inner.setContentsMargins(0, 0, 0, 0)
    inner.addWidget(label)
    inner.addStretch()
    scroll.setWidget(box)
    scroll.setStyleSheet('QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }'
                         ' QScrollBar:vertical { width: 6px; background: transparent; margin: 0; }'
                         ' QScrollBar::handle:vertical { background: #555; border-radius: 3px; min-height: 20px; }'
                         ' QScrollBar::add-line, QScrollBar::sub-line { height: 0; }')
    return scroll


def onto_screen(g, screens):
    """A saved position (QRect) moved onto the nearest screen's work area when its centre is on none of them
    (a monitor was unplugged or rearranged in Windows display settings). None if it's visible already."""
    c = g.center()
    if not screens or any(s.contains(c) for s in screens):
        return None
    s = min(screens, key=lambda s: (s.center() - c).manhattanLength())
    return (min(max(g.x(), s.left()), s.right() + 1 - g.width()),
            min(max(g.y(), s.top()), s.bottom() + 1 - g.height()))


class Widget(QWidget):
    title = ''

    def __init__(self, app, key, parent=None):
        """parent=None: a free-floating desktop widget. With a parent it's a docked panel (live dashboard):
        no dragging, no resize grip, no saved position."""
        super().__init__(parent)
        self.app, self.key, self.cfg, self.docked = app, key, app.cfg['widgets'][key], parent is not None
        self.setAttribute(Qt.WA_TranslucentBackground)
        if self.docked:  # live dashboard: condensed type like F1's own graphics (Bahnschrift ships with Windows)
            self.setStyleSheet(f'QLabel {{ color: #ECEDF1; font: 10pt "{COND}"; }} QLabel#t {{ font: 12pt "{COND_BOLD}"; color: #ECEDF1; }}')
        else:
            self.setStyleSheet('QLabel { color: #eee; font: 10pt "Segoe UI"; } QLabel#t { font: bold 10pt "Segoe UI"; color: #e10600; }')
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(*((16, 12, 16, 12) if self.docked else (12, 8, 12, 8)))
        head = QHBoxLayout()
        self.head = QLabel(self.title, objectName='t')
        head.addWidget(self.head, 1)
        if not self.docked:
            head.addWidget(QSizeGrip(self), 0, Qt.AlignTop | Qt.AlignRight)
        self.lay.addLayout(head)
        self.body = QLabel(wordWrap=True, textFormat=Qt.RichText, alignment=Qt.AlignTop | Qt.AlignLeft)
        self.lay.addWidget(self.body, 1)
        self.drag = None
        if self.docked:  # long tables get cut off at the bottom instead of stretching the dashboard window
            self.body.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Ignored)
            return
        g = self.cfg.get('geom')
        self.setGeometry(*(g or (60 + 40 * list(app.cfg['widgets']).index(key), 80 + 40 * list(app.cfg['widgets']).index(key), *self.size_hint)))
        self.keep_on_screen()
        self.apply_flags()
        self.apply_opacity()

    size_hint = (260, 320)

    def apply_flags(self):
        # Desktop layer is kept by App.keep_on_desktop, not Qt.WindowStaysOnBottomHint: Qt forces that hint's windows
        # back to the bottom on every z-order change, so they couldn't be lifted above the desktop after Win+D.
        layer = Qt.WindowStaysOnTopHint if self.app.cfg['on_top'] else Qt.WindowType(0)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool | layer)

    def text_opacity(self):
        return 1.0 if self.docked else self.app.cfg['text_opacity']

    def apply_opacity(self):
        """Text (and the map drawing) fades through an opacity effect on each child; the background is painted with
        its own alpha in paintEvent. Call again after adding children (SimPanel's controls)."""
        t = self.text_opacity()
        for c in self.findChildren(QWidget, options=Qt.FindDirectChildrenOnly):
            if isinstance(c, QSizeGrip):
                continue
            if t >= 1:
                c.setGraphicsEffect(None)
            else:
                fx = QGraphicsOpacityEffect(c)
                fx.setOpacity(t)
                c.setGraphicsEffect(fx)
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if self.docked:
            p.setPen(QPen(QColor('#20232C'), 1))
            p.setBrush(QColor('#121419'))
            p.drawRoundedRect(QRectF(self.rect()).adjusted(.5, .5, -.5, -.5), 14, 14)
            return
        p.setPen(Qt.NoPen)
        # alpha >= 1: fully transparent pixels are click-through on Windows, and the widget couldn't be dragged
        p.setBrush(QColor(16, 16, 22, max(1, round(255 * self.app.cfg['bg_opacity']))))
        p.drawRoundedRect(self.rect(), 10, 10)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and not self.docked and not self.app.cfg['locked']:
            self.drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self.drag is not None:
            self.move(e.globalPosition().toPoint() - self.drag)

    def mouseReleaseEvent(self, e):
        self.drag = None
        self.save_geom()

    def resizeEvent(self, e):
        self.save_geom()

    def keep_on_screen(self):
        pos = onto_screen(self.geometry(), [s.availableGeometry() for s in QApplication.screens()])
        if pos:
            self.move(*pos)
            self.save_geom()

    def save_geom(self):
        if self.docked:
            return
        g = self.geometry()
        self.cfg['geom'] = [g.x(), g.y(), g.width(), g.height()]
        self.app.save_soon()

    def refresh(self):
        """Static data changed (standings/schedule refetched)."""

    def tick(self):
        """Live state changed (called every ~0.5 s while a feed runs)."""


# ---- static widgets ------------------------------------------------------------------------------------------------

def session_kind(si):
    """'Race' / 'Sprint' for the points-paying sessions, else None. Sprint Qualifying / Shootout (2023+) are Type
    'Qualifying'; 2021's sprint was called 'Sprint Qualifying' but is Type 'Race'."""
    if si.get('Type') != 'Race':
        return None
    return 'Sprint' if 'Sprint' in si.get('Name', '') else 'Race'


def points_gain(st, kind, year):
    """{driver code: points} if the session finished in the current order. Race top 10 25..1, plus 1 for the fastest
    lap if it's in the top 10 (2019-2024 only); sprint 3-2-1 in 2021, top 8 8..1 from 2022."""
    table = ([3, 2, 1] if year == 2021 else [8, 7, 6, 5, 4, 3, 2, 1]) if kind == 'Sprint' else POINTS['Race']
    rows = [d for d in st.drivers() if d['pos'] < 99]
    gain = {d['code']: table[d['pos'] - 1] for d in rows if d['pos'] <= len(table)}
    best = min((d for d in rows if live.secs(d['best'])), key=lambda d: live.secs(d['best']), default=None)
    if kind == 'Race' and 2019 <= year <= 2024 and best and best['pos'] <= 10:
        gain[best['code']] += 1
    return gain


BASES = {}  # replayed session path -> data.standings_before() result, None while it loads


def replay_base(st, kind):
    """Standings before the replayed session, loaded once in the background (None until then / if unknown)."""
    path = st.d.get('SessionInfo', {}).get('Path', '')
    if not path[:4].isdigit():
        return None
    if path not in BASES:
        BASES[path] = None
        bg(lambda: data.standings_before(int(path[:4]), path.split('/')[1][:10], kind == 'Race'),
           lambda r: BASES.__setitem__(path, r))
    return BASES[path]


def projection(app):
    """Standings if the race/sprint finished now: {'drivers': [...], 'teams': [...]} rows with 'points' and 'delta',
    or None. F1's ChampionshipPrediction feed when it flows (live, and replays from mid-2019). Otherwise the standings
    before this session plus the points table for the running order. Live, that's only while Jolpica doesn't have this
    session's results yet (else they'd count twice); replays use the standings as they were before the session."""
    st = app.state
    if not st or not isinstance(st.feed, (live.LiveFeed, live.ReplayFeed)):
        return None
    kind = session_kind(st.d.get('SessionInfo', {}))
    if not kind:
        return None
    pred = st.prediction()
    if pred:
        return pred
    if st.session()['status'] not in ('Started', 'Finished', 'Finalised', 'Ends'):
        return None
    if isinstance(st.feed, live.ReplayFeed):
        base = replay_base(st, kind)
        if not isinstance(base, dict):
            return None
        drivers, teams = base['drivers'], base['teams']
    else:
        drivers, teams, counted = (app.static.get(k) for k in ('drivers', 'teams', 'counted'))
        if not isinstance(counted, dict) or counted.get(kind) or not isinstance(drivers, list):
            return None
        teams = teams if isinstance(teams, list) else []
    gain = points_gain(st, kind, st.season() or datetime.now(timezone.utc).year)
    now = {d['code']: d for d in st.drivers()}  # the session's own team colours (Jolpica's ids don't cover past teams)
    known = {d['code'] for d in drivers}
    rows = [{**d, 'colour': now[d['code']]['colour'] if d['code'] in now else d['colour']} for d in drivers] + \
           [{'code': c, 'team': '', 'colour': d['colour'], 'points': 0.0} for c, d in now.items() if c not in known and c in gain]
    rows = [{**d, 'points': d['points'] + gain.get(d['code'], 0), 'delta': gain.get(d['code'], 0)} for d in rows]
    team_gain, team_colour = {}, {}
    for d in rows:
        team_gain[d['team']] = team_gain.get(d['team'], 0) + d['delta']
        team_colour.setdefault(d['team'], d['colour'])
    trows = [{**t, 'colour': team_colour.get(t['name'], t['colour']), 'points': t['points'] + team_gain.get(t['name'], 0),
              'delta': team_gain.get(t['name'], 0)} for t in teams]
    return {'drivers': sorted(rows, key=lambda d: -d['points']), 'teams': sorted(trows, key=lambda t: -t['points'])}


def projection_label(app):
    return 'LIVE PROJECTION' if isinstance(app.state.feed, live.LiveFeed) else f"PROJECTION AS OF {app.state.season()} REPLAY"


def pts(row):
    return r(f"{row['points']:g}" + (f" <span style='color:#5f5'>+{row['delta']:g}</span>" if row.get('delta') else ''))


class DriverStandings(Widget):
    title = 'DRIVERS'

    def refresh(self):
        proj = projection(self.app)
        rows = proj['drivers'] if proj else self.app.static.get('drivers')
        if isinstance(rows, Exception) or rows is None:
            self.body.setText(f'<i>{rows or "loading…"}</i>')
            return
        self.head.setText(self.title + (f' · {projection_label(self.app)}' if proj else ''))
        self.body.setText(table(
            [i, f"#{d['colour']}", f"<b>{d['code']}</b>", f"<span style='color:#999'>{d['team']}</span>", pts(d)]
            for i, d in enumerate(rows[:self.cfg['n']], 1)))

    tick = refresh


class ConstructorStandings(Widget):
    title = 'CONSTRUCTORS'

    def refresh(self):
        proj = projection(self.app)
        rows = proj['teams'] if proj and proj['teams'] else self.app.static.get('teams')
        if isinstance(rows, Exception) or rows is None:
            self.body.setText(f'<i>{rows or "loading…"}</i>')
            return
        self.head.setText(self.title + (f' · {projection_label(self.app)}' if proj and proj['teams'] else ''))
        self.body.setText(table([i, f"#{d['colour']}", d['name'], pts(d)] for i, d in enumerate(rows[:self.cfg['n']], 1)))

    tick = refresh


class NextRace(Widget):
    title = 'NEXT RACE'
    size_hint = (280, 230)

    def __init__(self, app, key, parent=None):
        super().__init__(app, key, parent)
        self.timer = QTimer(self, timeout=self.refresh, interval=1000)
        self.timer.start()

    def refresh(self):
        race = self.app.static.get('next')
        if isinstance(race, Exception) or race is None:
            self.body.setText(f'<i>{race or "loading…"}</i>')
            return
        now = datetime.now(timezone.utc)
        upcoming = [s for s in race['sessions'] if s[1] > now]
        on = data.live_session(race, now)
        if on:
            cd = f"<p style='font-size:15pt;color:#5f5'><b>{on} LIVE</b></p>"
        elif upcoming:
            left = int((upcoming[0][1] - now).total_seconds())
            cd = f"<p style='font-size:15pt'><b>{upcoming[0][0]}</b> in {left // 86400}d {left % 86400 // 3600:02}:{left % 3600 // 60:02}:{left % 60:02}</p>"
        else:
            cd = "<p style='font-size:15pt;color:#5f5'><b>RACE ON</b></p>"
        self.body.setText(
            f"<b>R{race['round']} · {race['name']}</b><br><span style='color:#999'>{race['circuit']}, {race['place']}</span>{cd}"
            + table([label, t.astimezone().strftime('%a %d %b'), r(t.astimezone().strftime('%H:%M'))] for label, t in race['sessions']))


# ---- live widgets --------------------------------------------------------------------------------------------------

def _need_feed(w):
    if not w.app.state:
        w.body.setText('<i>No session. Tray → Live tracker, or Tray → Replay.</i>')
        return True
    return False


class TrackMap(Widget):
    title = 'TRACK'
    size_hint = (460, 380)

    def __init__(self, app, key, parent=None):
        super().__init__(app, key, parent)
        self.outline = (None, None, None)  # (track, size, pixel ratio, tarmac) -> outline pixmap, outline on screen
        self.hist, self.hist_of = collections.deque(), None  # (feed time, {car: (x, y)}) for the pit ghost; its State
        self.blink = False
        # ~60 fps while cars glide (the feed ticks every 0.5 s). Precise: a coarse timer lands on Windows' 15.6 ms ticks,
        # alternating 31 and 47 ms frames, which judders
        QTimer(self, timeout=self.frame, interval=16, timerType=Qt.PreciseTimer).start()

    def frame(self):
        st = self.app.state
        if not st or not self.isVisible():
            return
        blink = time.monotonic() % 1 < 0.5
        if st.glide_at() is not None or (blink != self.blink and str(st.d.get('TrackStatus', {}).get('Status')) in BLINK):
            self.update()

    def tick(self):
        st = self.app.state
        self.body.setText('' if st and st.track else '<i>No session. Tray → Live tracker, or Tray → Replay.</i>' if not st
                          else f'<i>{getattr(st.feed, "status", "")}<br>waiting for track outline…</i>')
        if st:
            s = st.session()
            self.head.setText(f"{s['name'] or 'TRACK'}" + (f" · L{s['lap']}/{s['total']}" if s['total'] else ''))
        self.update()

    def paintEvent(self, e):
        super().paintEvent(e)
        st = self.app.state
        if not st or not st.track:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setOpacity(self.text_opacity())
        status = str(st.d.get('TrackStatus', {}).get('Status'))
        self.blink = time.monotonic() % 1 < 0.5
        tint = STATUS_TINT.get(status)
        if tint and (status not in BLINK or self.blink):
            p.setPen(QPen(QColor(tint), 3))
            p.setBrush(Qt.NoBrush)
            radius = 14 if self.docked else 10
            p.drawRoundedRect(QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5), radius, radius)

        k = max(1.0, min(self.width(), self.height()) / 380)  # markers grow with the map (big in the live dashboard)
        a = np.radians(st.track.rotation)  # same convention as FastF1's rotate()
        rot = np.array([[np.cos(a), np.sin(a)], [-np.sin(a), np.cos(a)]])
        pts = st.track.xy @ rot
        lo, span = pts.min(0), np.ptp(pts, 0) + 1e-9
        m = 8 * k  # half the verge: the outline's centre line fits the box, the verge must stay off the border
        box = QRectF(self.rect()).adjusted(18 + m, 40 + m, -18 - m, -28 - m)
        sc = min(box.width() / span[0], box.height() / span[1])
        ox, oy = box.left() + (box.width() - span[0] * sc) / 2, box.bottom() - (box.height() - span[1] * sc) / 2

        def to(q):
            return QPointF(ox + (q[0] - lo[0]) * sc, oy - (q[1] - lo[1]) * sc)

        def screen_dir(deg):  # a direction in circuit coordinates (degrees, the circuit API's angle) on screen
            v = np.array([np.cos(np.radians(deg)), np.sin(np.radians(deg))]) @ rot
            return QPointF(v[0], -v[1])

        key = (id(st.track), self.width(), self.height(), self.devicePixelRatioF(), TARMAC.get(status))
        if self.outline[0] != key:  # drawn once per track, size and status colour, then reused every frame
            dpr = self.devicePixelRatioF()
            pm = QPixmap(round(self.width() * dpr), round(self.height() * dpr))
            pm.setDevicePixelRatio(dpr)
            pm.fill(Qt.transparent)
            on_screen = [to(q) for q in pts]
            path = QPainterPath(on_screen[0])
            for q in on_screen[1:]:
                path.lineTo(q)
            o = QPainter(pm)
            o.setRenderHint(QPainter.Antialiasing)
            for colour, width in (('#1C1F27', 16), (TARMAC.get(status, '#3A3F4D'), 8)):  # dark verge, then the tarmac
                o.setPen(QPen(QColor(colour), width * k, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
                o.drawPath(path)
            o.setFont(QFont(COND_BOLD, max(5, round(6 * k))))
            o.setPen(QColor('#6B7080'))
            for num, x, y, deg in st.track.corners:  # turn numbers, beside the verge on the circuit API's side
                c = to(np.array([x, y]) @ rot) + screen_dir(deg) * 15 * k
                c = QPointF(min(max(c.x(), 12 + 10 * k), self.width() - 12 - 10 * k),  # kept inside the border
                            min(max(c.y(), 34 + 6 * k), self.height() - 24 - 6 * k))
                o.drawText(QRectF(c - QPointF(10 * k, 6 * k), QSizeF(20 * k, 12 * k)), Qt.AlignCenter, str(num))
            o.end()
            self.outline = (key, pm, on_screen)
        p.drawPixmap(0, 0, self.outline[1])
        self.draw_yellows(p, st, self.outline[2], k)

        cars, gps = st.positions()
        self.draw_ghost(p, st, cars, to, rot, k)
        dl = st.d.get('DriverList', {})
        order = {d['drv']: d['pos'] for d in st.drivers()}
        f = k * self.app.cfg.get('label_scale', 1.0)  # name size, Settings
        mine = set(my_codes(self.app))  # my drivers: a white ring and a white name, the others' names dimmer
        p.setFont(QFont(COND_BOLD, max(5, round(8 * f))))
        cars = [(n, to(np.array([x, y]) @ rot), pit, dl.get(n) if isinstance(dl.get(n), dict) else {})
                for n, x, y, pit in sorted(cars, key=lambda c: order.get(c[0], 99))]
        for n, pt, pit, info in reversed(cars):  # leader's dot drawn last (on top)
            p.setPen(QPen(QColor('#0B0C10'), 1.5))
            p.setBrush(QColor(110, 110, 110) if pit else QColor(STATUS_TINT['4']) if n in live.SAFETY_CARS
                       else QColor('#' + info.get('TeamColour', '888888')))
            p.drawEllipse(pt, 5 * k, 5 * k)
            if info.get('Tla') in mine:
                p.setPen(QPen(QColor('#FFFFFF'), 2 * k))
                p.setBrush(Qt.NoBrush)
                p.drawEllipse(pt, 8.5 * k, 8.5 * k)
        groups = []  # bunched cars: stack up to 4 labels in running order (my drivers first), then "+N"
        for n, pt, pit, info in cars:
            label = 'SC' if n in live.SAFETY_CARS else info.get('Tla') or n
            g = next((g for g in groups if (g[0] - pt).manhattanLength() < 14 * f), None)
            if g is None:
                groups.append(g := [pt, []])
            g[1].append((label, pit))
        for pt, labels in groups:
            labels.sort(key=lambda lp: lp[0] not in mine)  # stable: running order otherwise
            for i, (label, pit) in enumerate(labels[:4]):
                p.setPen(QColor(140, 140, 140) if pit else QColor('#FFFFFF') if label in mine or not mine
                         else QColor(185, 188, 196))
                p.drawText(pt + QPointF(7 * k, (4 + 10 * i) * f), label if i < 3 or len(labels) == 4 else f'+{len(labels) - 3}')
        w = st.d.get('WeatherData', {})
        if w.get('WindDirection') not in (None, ''):
            self.draw_wind(p, rot, float(w['WindDirection']), w.get('WindSpeed', '?'), k)

        p.setFont(QFont(COND, 9))
        p.setPen(QColor('#3DDC84') if gps else QColor('#fc5'))
        p.drawText(QRectF(self.rect()).adjusted(12, 0, -12, -8), Qt.AlignBottom | Qt.AlignLeft,
                   f"{'GPS' if gps else 'ESTIMATED'} · {getattr(st.feed, 'status', '')} · {st.session()['track']}")

    def focus(self):
        """Racing number picked in the strategy sim (the live dashboard's), or None."""
        sim_panel = getattr(self.app, 'sim', None)
        return sim_panel.selected() if sim_panel else None

    def draw_yellows(self, p, st, on_screen, k):
        """Marshal sectors under a yellow flag, painted over the tarmac (double yellow wider)."""
        n = len(on_screen) - 1
        for sector, flag in st.yellow_sectors().items():
            if sector not in st.track.marshal:
                continue
            i, j = st.track.marshal[sector]
            idx = list(range(i, j + 1)) if j > i else list(range(i, n)) + list(range(0, j + 1))  # the last one wraps
            path = QPainterPath(on_screen[idx[0]])
            for q in idx[1:]:
                path.lineTo(on_screen[q])
            p.setPen(QPen(QColor(STATUS_TINT['4']), (12 if flag == 'DOUBLE YELLOW' else 8) * k, Qt.SolidLine, Qt.FlatCap,
                          Qt.RoundJoin))
            p.setBrush(Qt.NoBrush)
            p.drawPath(path)

    def draw_ghost(self, p, st, cars, to, rot, k):
        """Where the focus car would be if it pitted now: its own spot one pit loss ago (the field moves on at about
        its pace, SC or not), a ring in its team colour. Races only."""
        if self.hist_of is not st:
            self.hist, self.hist_of = collections.deque(), st
        now = st.now()
        if self.hist and now < self.hist[-1][0]:  # a replay jumped back
            self.hist.clear()
        if not self.hist or now - self.hist[-1][0] >= 0.2:
            self.hist.append((now, {n: (x, y) for n, x, y, pit in cars}))
        while self.hist and self.hist[0][0] < now - 90:
            self.hist.popleft()
        me = self.focus()
        if not me or not st.session()['total'] or not hasattr(self.app, 'model'):
            return
        under = {'4': 'sc', '6': 'vsc', '7': 'vsc'}.get(str(st.d.get('TrackStatus', {}).get('Status')), 'normal')
        then = now - st.pit_loss(self.app.model())[under]
        if then < self.hist[0][0]:
            return  # not watched long enough yet
        i = next(i for i, h in enumerate(self.hist) if h[0] >= then)
        (t0, a), (t1, b) = self.hist[max(i - 1, 0)], self.hist[i]
        if me not in a or me not in b:
            return
        f = (then - t0) / (t1 - t0) if t1 > t0 else 0
        x, y = np.array(a[me]) * (1 - f) + np.array(b[me]) * f
        info = st.d.get('DriverList', {}).get(me)
        colour = info.get('TeamColour', '888888') if isinstance(info, dict) else '888888'
        pt = to(np.array([x, y]) @ rot)
        p.setPen(QPen(QColor('#' + colour), 2.5 * k))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(pt, 7 * k, 7 * k)
        p.setFont(QFont(COND_BOLD, max(5, round(7 * k))))
        p.setPen(QColor(235, 235, 235))
        p.drawText(pt + QPointF(9 * k, -6 * k), 'pit')

    def draw_wind(self, p, rot, frm, speed, k):
        """Compass in the top-right corner, turned like the map: N, and an arrow pointing where the wind blows.
        Position x/y run east/north (fits OpenStreetMap within 2° at 5 circuits). F1's WindDirection is where the wind
        comes from, like a weather report (matches Open-Meteo history for 2025 races, not the opposite way round)."""
        def screen(bearing):  # compass bearing -> unit vector on screen (screen y points down)
            v = np.array([np.sin(np.radians(bearing)), np.cos(np.radians(bearing))]) @ rot
            return QPointF(v[0], -v[1])
        r = 16 * k
        c = QPointF(self.width() - 22 - r, 52 + r)
        p.setPen(QPen(QColor(120, 120, 130), 1))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(c, r, r)
        p.setFont(QFont('Segoe UI', max(6, round(6 * k)), QFont.Bold))
        p.setPen(QColor(170, 170, 180))
        p.drawText(QRectF(c + screen(0) * (r + 7 * k) - QPointF(8 * k, 8 * k), QSizeF(16 * k, 16 * k)), Qt.AlignCenter, 'N')
        d = screen(frm + 180)
        tip, side = c + d * (r - 2 * k), QPointF(-d.y(), d.x())
        blue = QColor('#7cc4ff')
        p.setPen(QPen(blue, 2 * k, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(c - d * (r - 4 * k), tip - d * 4 * k)
        p.setPen(Qt.NoPen)
        p.setBrush(blue)
        p.drawPolygon(QPolygonF([tip, tip - d * 7 * k + side * 4 * k, tip - d * 7 * k - side * 4 * k]))
        p.setFont(QFont('Segoe UI', 8))
        p.setPen(QColor(200, 200, 200))
        card = 'N NE E SE S SW W NW'.split()[round(frm / 45) % 8]
        p.drawText(QRectF(self.width() - 168, c.y() + r + 9 * k, 160, 16), Qt.AlignRight | Qt.AlignTop,
                   f'wind {speed} m/s from {card}')


def best_and_gap(ln, q):
    """Best lap and gap to the fastest: for the current part in qualifying, else for the session."""
    if q:
        return ((seq_at(ln.get('BestLapTimes'), q['part'] - 1) or {}).get('Value', ''),
                (seq_at(ln.get('Stats'), q['part'] - 1) or {}).get('TimeDiffToFastest', ''))
    return (ln.get('BestLapTime') or {}).get('Value', ''), ln.get('TimeDiffToFastest', '')


def pos_and_code(d, ln, q):
    """(position, code, knocked out): position red in the drop zone, code greyed once out of the session."""
    grey = "<span style='color:#777'>{}</span>".format
    ko = ln.get('KnockedOut')
    pos = d['pos'] if d['pos'] < 99 else ''
    if q and q['safe'] and not ko and pos and pos > q['safe']:
        pos = f"<span style='color:#ff4040'><b>{pos}</b></span>"  # knocked out if the part ended now
    return pos, f"<b>{d['code']}</b>" if not (ko or d['out']) else grey(d['code']), ko


class TimingTower(Widget):
    title = 'TIMING'
    size_hint = (330, 560)

    def contextMenuEvent(self, e):
        """Right-click: pin a battle card (BATTLES) for any two drivers."""
        st = self.app.state
        ds = [d for d in st.drivers() if d['pos'] < 99 and not d['out']] if st else []
        if len(ds) < 2:
            return
        menu = QMenu(self)
        sub = menu.addMenu('Add battle')
        for a in ds:
            pick = sub.addMenu(f"P{a['pos']} {a['code']}")
            for b in ds:
                if b is not a:
                    pick.addAction(f"vs P{b['pos']} {b['code']}", lambda a=a['code'], b=b['code']: owner(self.app).set(
                        'battles', owner(self.app).cfg['battles'] + [[a, b]]) if [a, b] not in owner(self.app).cfg['battles'] else None)
        menu.exec(e.globalPos())

    def tick(self):
        if _need_feed(self):
            return
        st, s = self.app.state, self.app.state.session()
        q, left = st.quali(), st.clock()
        self.head.setText(f"{s['name'] or 'TIMING'}" + (f" · L{s['lap']}/{s['total']}" if s['total'] else '')
                          + (f" · {q['label']}" if q else '') + (f' · {mmss(left)} left' if left and not s['total'] else ''))
        race = s['type'] in ('Race', 'Sprint')
        wide = self.docked and race  # the live dashboard has room for leader gap and best lap too
        grey = "<span style='color:#777'>{}</span>".format
        lines = st.d.get('TimingData', {}).get('Lines', {})
        rows = [['', '', '', r(grey('INT')), r(grey('LEADER')), r(grey('LAST')), r(grey('BEST')), grey('TYRE'), grey('STOPS')]] if wide else []
        fastest = min((d['best'] for d in st.drivers() if d['best']), key=lambda b: live.secs(b) or 1e9, default=None)
        for d in st.drivers()[:self.cfg['n']]:
            letter, col = tyre_style(d['cmp'], st)
            if not race:  # practice / qualifying: best lap of the current part, drop zone, knocked-out cars greyed
                pos, code, ko = pos_and_code(d, lines.get(d['drv'], {}), q)
                best = best_and_gap(lines.get(d['drv'], {}), q)[0]
                rows.append([grey(pos) if ko else pos, '#444444' if ko else f"#{d['colour']}", code, r(grey(best) if ko else best),
                             r(f"<span style='color:#999'>{'' if ko else d['last']}</span>"),
                             grey('out') if ko else f"<span style='color:{col}'><b>{letter}</b></span>{d['age']}",
                             'PIT' if d['pit'] and not ko else ''])
                continue
            gap = d['int'] if d['pos'] > 1 else d['laps'] and f"L{d['laps']}"
            rows.append([d['pos'], f"#{d['colour']}", f"<b>{d['code']}</b>" if not d['out'] else f"<s>{d['code']}</s>",
                         r(gap or ''), *([r(grey(d['gap'] if d['pos'] > 1 else ''))] if wide else []),
                         r(f"<span style='color:#999'>{d['last']}</span>"),
                         *([r(f"<span style='color:{PURPLE}'>{d['best']}</span>" if d['best'] == fastest else grey(d['best']))] if wide else []),
                         f"<span style='color:{col}'><b>{letter}</b></span>{d['age']}",
                         'PIT' if d['pit'] else grey(d['stops'] or '')])
        status = getattr(st.feed, 'status', '')
        mine, shown = set(my_codes(self.app)), st.drivers()[:self.cfg['n']]
        hi = {i + bool(wide) for i, d in enumerate(shown) if d['code'] in mine}
        self.body.setText(f"<span style='color:#999'>{status} · {s['track']} {s['status']}</span>" + table(rows, self.docked, hi))


class SimPanel(Widget):
    title = 'STRATEGY SIM'
    size_hint = (400, 430)

    def __init__(self, app, key, parent=None):
        super().__init__(app, key, parent)
        self.drv, self.lap, self.cmp = QComboBox(), QSpinBox(minimum=1, maximum=99), QComboBox()
        self.cmp.addItems(['MEDIUM', 'HARD', 'SOFT', 'INTERMEDIATE', 'WET'])
        go, best = QPushButton('What if', objectName='primary'), QPushButton('Best window')
        go.clicked.connect(lambda: self.run(False))
        best.clicked.connect(lambda: self.run(True))
        row = QHBoxLayout()
        pit = QLabel('pit on lap')
        pit.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)  # else it takes a share of the spare width
        for w, stretch in ((self.drv, 2), (pit, 0), (self.lap, 1), (self.cmp, 2)):
            row.addWidget(w, stretch)
        # weather what-if: the sim holds today's track (what most cars are on) unless told it dries or rains
        self.wx, self.wx_lap = QComboBox(), QSpinBox(minimum=1, maximum=99)
        self.wx.addItems(['track stays as now', 'track dries from lap', 'rain (inters) from lap'])
        self.wx.currentIndexChanged.connect(lambda i: self.wx_lap.setEnabled(i > 0))
        self.wx_lap.setEnabled(False)
        row_wx = QHBoxLayout()
        row_wx.addWidget(self.wx, 4)
        row_wx.addWidget(self.wx_lap, 1)
        # neutralisation what-if: a safety car / VSC / red flag on lap N (an SC or VSC out right now is always used)
        self.ev, self.ev_lap = QComboBox(), QSpinBox(minimum=1, maximum=99)
        self.ev.addItems(['no extra SC / VSC', 'safety car on lap', 'VSC on lap', 'red flag on lap'])
        self.ev.currentIndexChanged.connect(lambda i: self.ev_lap.setEnabled(i > 0))
        self.ev_lap.setEnabled(False)
        row_ev = QHBoxLayout()
        row_ev.addWidget(self.ev, 4)
        row_ev.addWidget(self.ev_lap, 1)
        row2 = QHBoxLayout()
        row2.addWidget(go)
        row2.addWidget(best)
        self.lay.insertLayout(1, row)
        self.lay.insertLayout(2, row_wx)
        self.lay.insertLayout(3, row_ev)
        self.lay.insertLayout(4, row2)
        self.out = QLabel(wordWrap=True, textFormat=Qt.RichText, alignment=Qt.AlignTop | Qt.AlignLeft)
        self.lay.addWidget(scrolled(self.out), 2)
        self.setStyleSheet(self.styleSheet() + ' QComboBox, QSpinBox, QPushButton { font: 9pt "Segoe UI"; }')
        self.priors, self.busy = {}, False  # meeting path -> compound priors from that weekend's practice

    def tick(self):
        if _need_feed(self):
            return
        st, s = self.app.state, self.app.state.session()
        ds = [d for d in st.drivers() if not d['out']]
        codes = [f"{d['code']} {d['drv']}" for d in ds]
        if [self.drv.itemText(i) for i in range(self.drv.count())] != codes:
            keep = self.drv.currentText()
            self.drv.clear()
            self.drv.addItems(codes)
            first = next((c for m in my_codes(self.app) for c in codes if c.split()[0] == m), None)
            self.drv.setCurrentText(keep if keep in codes else first or keep)  # opens on my first driver
        if not s['total']:
            self.body.setText('<i>Strategy sim needs a race or sprint (lap count).</i>')
            return
        lap = s['lap'] or 1
        for w in (self.lap, self.wx_lap, self.ev_lap):
            w.setMinimum(lap)
            w.setMaximum(max(lap, s['total'] - 1))
        me, loss = self.selected(), self.loss()
        src = loss['source'] + (f", {loss['n']} stops" if loss['n'] else '')
        under = {'4': 'sc', '6': 'vsc', '7': 'vsc'}.get(str(st.d.get('TrackStatus', {}).get('Status')))
        txt = (f"Lap {lap}/{s['total']} · est. car mass {sim.mass_kg(lap, s['total'], self.app.model()):.0f} kg"
               f" · pit loss {loss['normal']:.1f}s ({src})" + (f", {loss[under]:.1f}s under {under.upper()}" if under else '')
               + f" · {len(st.laps)} laps logged")
        if me and len(ds) > 1:
            pos, a, ga, b, gb = sim.rejoin(ds, me, loss[under or 'normal'], sim.lap_time(st.laps))
            txt += (f"<br>Pit <b>now</b> → rejoin <b>P{pos}</b>" + (f", {ga:.1f}s behind {a}" if a else '')
                    + (f", {gb:.1f}s ahead of {b}" if b else ''))
        self.body.setText(txt)

    def selected(self):
        t = self.drv.currentText()
        return t.split()[-1] if t else None

    def loss(self):
        return self.app.state.pit_loss(self.app.model())

    def run(self, window):
        st, me = self.app.state, self.selected()
        if not st or not me or self.busy or not st.session()['total']:
            return
        self.busy = True
        laps, ds, s, loss, m = list(st.laps), [d for d in st.drivers() if not d['out']], st.session(), self.loss(), self.app.model()
        m = {**m, 'two_compounds': 'Sprint' not in s['name']}  # sprints have free tyre choice
        names = sim.relative_compounds(laps)  # 2018: Pirelli's names -> SOFT/MEDIUM/HARD for the model
        laps, ds = sim.relabel(laps, names), sim.relabel(ds, names)
        lap, pit_lap, cmp = s['lap'] or 1, self.lap.value(), self.cmp.currentText()
        cond = {0: None, 1: {self.wx_lap.value(): 'DRY'}, 2: {self.wx_lap.value(): 'WET'}}[self.wx.currentIndex()]
        events = {self.ev_lap.value(): sim.NEUTRAL[self.ev.currentIndex() - 1]} if self.ev.currentIndex() else None
        now = st.neutral_now(m)
        self.scenario = ', '.join(x for x in (
            now and f"{now[0]} out now (~{now[1]} lap{'s' if now[1] > 1 else ''} left)",
            events and f"{self.ev.currentText().replace(' on lap', '')} on lap {self.ev_lap.value()}",
            cond and ('rain' if 'WET' in cond.values() else 'track dries') + f" from lap {self.wx_lap.value()}") if x)
        wet = 'WET' in (cond or {}).values() or sim.track_now(ds) == 'WET'
        meeting = st.d.get('SessionInfo', {}).get('Path', '').rsplit('/', 2)[0]
        self.out.setText('<i>simulating…' + ('' if meeting in self.priors else ' (first run loads this weekend\'s practice data)') + '</i>')

        def work():
            if meeting not in self.priors:
                fp = [p for label, p in data.past_sessions(int(meeting[:4] or 0) or None, 999)
                      if meeting and p.startswith(meeting) and 'Practice' in p]
                fp_laps = [{**l, 'sess': i} for i, p in enumerate(fp) for l in live.laps_from_archive(p, track=False)[1]]
                self.priors[meeting] = sim.priors_from(fp_laps, s['total'], m) if fp_laps else m['prior']
            model = sim.fit(laps, s['total'], {**m, 'prior': self.priors[meeting]})
            kw = {'pit_loss': loss, 'lap_s': sim.lap_time(laps), 'conditions': cond, 'neutral_now': now, 'events': events}
            if window:
                return 'window', sim.best_window(ds, lap, model, me, **kw,
                                                 compounds=('MEDIUM', 'HARD', 'SOFT') + (('INTERMEDIATE',) if wet else ()))
            return 'whatif', (pit_lap, cmp, sim.what_if(ds, lap, model, me, pit_lap, cmp, **kw))

        bg(work, self.show_result)

    def scenario_note(self):
        return f"<br><span style='color:#e8c547'>with {self.scenario}</span>" if getattr(self, 'scenario', '') else ''

    def show_result(self, res):
        self.busy = False
        if isinstance(res, Exception):
            self.out.setText(f'<span style="color:#f66">sim failed: {res}</span>')
            return
        kind, x = res
        if kind == 'window':
            self.out.setText('<b>Best stop windows</b> (mean finish)' + self.scenario_note() + table(
                [f'L{L}', f"<span style='color:{tyre_style(c, self.app.state)[1]}'>{c}</span>", r(f'P{mean:.2f}')] for mean, L, c in x[:8]))
            return

        def dist(a):  # the 4 likeliest finishes as a bar, likeliest brightest
            v, n = np.unique(a, return_counts=True)
            top = sorted(zip(n, v), reverse=True)[:4]
            cells = ''.join(f"<td width='{100 * c / len(a):.0f}%' bgcolor='{shade}' align=center>"
                            f"<span style='color:{'#0B0C10' if i == 0 else '#ECEDF1'};font-size:8pt'>"
                            f"{f'P{int(p)} {c / len(a):.0%}' if c / len(a) >= 0.12 else ''}</span></td>"
                            for i, ((c, p), shade) in enumerate(zip(top, ('#3DDC84', '#2A8F58', '#1E5E3C', '#173B28'))))
            return f"<table width=100% cellspacing=1 cellpadding=1><tr>{cells}<td></td></tr></table>"
        pit_lap, cmp, x = x
        b, p = x['base'], x['plan']
        delta = b.mean() - p.mean()
        self.out.setText(self.scenario_note() + table([
            ['', r('mean'), 'most likely finishes'],
            ['Current plan', r(f'P{b.mean():.2f}'), dist(b)],
            [f'Pit L{pit_lap} {cmp}', r(f'P{p.mean():.2f}'), dist(p)],
        ]) + f"<br><b style='color:{'#5f5' if delta > 0.05 else '#f66' if delta < -0.05 else '#ccc'}'>"
             f"{'gains' if delta >= 0 else 'loses'} {abs(delta):.2f} places on average</b>"
             f"<br><span style='color:#777'>Model: fitted lap times, tyre deg, fuel burn, SC/VSC risk, overtaking odds "
             f"(calibrated on this regulation era's races). Not modelled: ERS, tyre temps, team intent, weather forecasts.</span>")


PURPLE, GREEN, YELLOW = '#b14cff', '#2ecc71', '#e8c547'
FLAG = {'GREEN': GREEN, 'CLEAR': GREEN, 'YELLOW': '#ffd400', 'DOUBLE YELLOW': '#ffd400', 'RED': '#ff2020',
        'BLUE': '#2f7cff', 'CHEQUERED': '#ffffff', 'BLACK AND WHITE': '#cccccc', 'BLACK AND ORANGE': '#ff9900'}


def segment(status):
    """Mini-sector colour from its Status bit flags (as decoded by undercut-f1, and matching the archive):
    2048 passed (yellow), +1 personal best (green), +2 overall best (purple), +16 in the pit lane; 0 not reached yet."""
    st = int(status or 0)
    return '#666666' if st & 16 else PURPLE if st & 2 else GREEN if st & 1 else YELLOW if st & 2048 else '#2a2a33'


def mmss(t):
    return f'{int(t) // 60:02}:{int(t) % 60:02}' if t is not None else ''


def tyre(cmp, age, st):
    letter, col = tyre_style(cmp, st)
    return f"<span style='color:{col}'><b>{letter}</b></span>{age}"


def seq_at(x, i):
    x = live.seq(x)
    return x[i] if 0 <= i < len(x) and isinstance(x[i], dict) else None


def stints(st, drv):
    """A driver's tyre stints. F1 opens a new one when a car comes through the pits on the same tyres (red flag:
    TyresNotChanged '1'); those carry on the stint before."""
    out = []
    for x in live.seq(st.d.get('TimingAppData', {}).get('Lines', {}).get(drv, {}).get('Stints')):
        if not x:
            continue
        if out and str(x.get('TyresNotChanged')) == '1':
            out[-1] = {**out[-1], 'TotalLaps': x.get('TotalLaps', out[-1].get('TotalLaps'))}
        else:
            out.append(x)
    return out


class SectorTower(Widget):
    """Practice/qualifying timing: best lap, gap, the current lap's sectors with mini-sectors, last lap, tyre.
    F1 resets the mini-sectors and clears the sector times as a car starts a lap, so they're always the current lap.
    Qualifying: best and gap for the current part, drop zone in red, knocked-out drivers greyed."""
    title = 'TIMING'

    def tick(self):
        if _need_feed(self):
            return
        st, s = self.app.state, self.app.state.session()
        q, left = st.quali(), st.clock()
        self.head.setText(f"{s['name'] or 'TIMING'}" + (f" · {q['label']}" if q else '') + (f' · {mmss(left)} left' if left else ''))
        lines = st.d.get('TimingData', {}).get('Lines', {})
        grey = "<span style='color:#777'>{}</span>".format
        rows = [['', '', '', r(grey('BEST')), r(grey('GAP')), r(grey('S1')), r(grey('S2')), r(grey('S3')), r(grey('LAST')),
                 grey('TYRE'), r(grey('LAPS')), '']]
        for d in st.drivers():
            ln = lines.get(d['drv'], {})
            best, gap = best_and_gap(ln, q)
            pos, code, ko = pos_and_code(d, ln, q)
            if ko:
                rows.append([grey(pos), '#444444', code, r(grey(best)), '', '', '', '', '', '', '', grey('out')])
                continue
            sectors = []
            for sec in live.seq(ln.get('Sectors')):
                sec = sec if isinstance(sec, dict) else {}
                squares = ''.join(f"<span style='color:{segment(g.get('Status'))}'>▮</span>"
                                  for g in live.seq(sec.get('Segments')) if isinstance(g, dict))
                col = PURPLE if sec.get('OverallFastest') else GREEN if sec.get('PersonalFastest') else '#dddddd'
                sectors.append(r(f"<span style='font-size:7pt'>{squares}</span>&nbsp;<span style='color:{col}'>{sec.get('Value', '')}</span>"))
            last = ln.get('LastLapTime') or {}
            lcol = PURPLE if last.get('OverallFastest') else GREEN if last.get('PersonalFastest') else '#999999'
            rows.append([pos, f"#{d['colour']}", code, r(best), r(grey(gap if d['pos'] > 1 else '')),
                         *(sectors + [''] * 3)[:3], r(f"<span style='color:{lcol}'>{last.get('Value', '')}</span>"),
                         tyre(d['cmp'], d['age'], st), r(grey(d['laps'] or '')), 'PIT' if d['pit'] else ''])
        mine = set(my_codes(self.app))
        hi = {i + 1 for i, d in enumerate(st.drivers()) if d['code'] in mine}
        self.body.setText(f"<span style='color:#999'>{getattr(st.feed, 'status', '')} · {s['track']} {s['status']}</span>"
                          f"<div style='font-size:9pt'>{table(rows, self.docked, hi)}</div>")  # 9pt: 22 rows fit


class TyrePanel(Widget):
    """Practice/qualifying: each driver's current tyre, its age, new or used, and how many sets so far."""
    title = 'TYRES'

    def tick(self):
        if _need_feed(self):
            return
        st = self.app.state
        cells = []
        for d in st.drivers():
            ss = stints(st, d['drv'])
            cur = ss[-1] if ss else {}
            new = str(cur.get('New')).lower() == 'true'
            cells.append([f"#{d['colour']}", f"<b>{d['code']}</b>", tyre(cur.get('Compound'), cur.get('TotalLaps', ''), st) + '&nbsp;&nbsp;' if cur else '',  # Qt measures bold short
                          ("<span style='color:#5f5'>new</span>" if new else "<span style='color:#888'>used</span>") if cur else '',
                          r(f"<span style='color:#888'>{len(ss)}</span>") if ss else ''])
        half = (len(cells) + 1) // 2  # two drivers per row: 22 don't fit one under the other
        rows = [a + [''] + (cells[half + i] if half + i < len(cells) else []) for i, a in enumerate(cells[:half])]
        self.body.setText("<span style='color:#777'>tyre + laps on it · new/used · sets so far</span>"
                          f"<div style='font-size:9pt'>{table(rows, self.docked)}</div>")


class StintPanel(Widget):
    """Race: every driver's tyre stints so far as coloured bars over the race distance, plus stops and pit times."""
    title = 'TYRE STINTS'

    def tick(self):
        if _need_feed(self):
            return
        st, s = self.app.state, self.app.state.session()
        self.head.setText(f"{s['name'] or 'TYRE STINTS'}" + (f" · L{s['lap']}/{s['total']}" if s['total'] else ''))
        ds = st.drivers()
        total = max([s['total'] or 0, 1] + [int(d['laps'] or 0) for d in ds])
        stops = {}
        for x in st.stops():
            stops.setdefault(x['drv'], []).append(x)
        grey = "<span style='color:#777'>{}</span>".format
        rows = [['', '', '', grey('STINTS: laps on the set, * = used set'), r(grey('STOPS')), r(grey('LAST STOP'))]]
        for d in ds:
            cells, used_pct = [], 0.0
            for x in stints(st, d['drv']):
                n = max(int(x.get('TotalLaps') or 0) - int(x.get('StartLaps') or 0), 0)  # TotalLaps = tyre age
                if not n:
                    continue
                col = tyre_style(x.get('Compound'), st)[1]
                pct = 100 * n / total
                used_pct += pct
                ink = '#fff' if x.get('Compound') == 'WET' else '#000'  # black is hard to read on the wet blue
                cells.append(f"<td width='{pct:.1f}%' bgcolor='{col}' align=center><span style='color:{ink};font-size:9pt'>"
                             f"<b>{n}{'' if str(x.get('New')).lower() == 'true' else '*'}</b></span></td>")
            if used_pct < 99.5:
                cells.append(f"<td width='{100 - used_pct:.1f}%'></td>")
            bar = "<table width=100% cellspacing=1 cellpadding=2><tr>" + ''.join(cells) + "</tr></table>"
            mine = stops.get(d['drv'], [])
            last = mine[-1].get('stationary') if mine else None
            rows.append([d['pos'] if d['pos'] < 99 else '', f"#{d['colour']}",
                         f"<b>{d['code']}</b>" if not d['out'] else f"<s>{d['code']}</s>", bar,
                         r(d['stops'] or ''), r(grey(f'{last:.1f}s' if last else ''))])
        self.body.setText(f"<div style='font-size:9pt'>{table(rows, self.docked)}</div>"
                          "<span style='color:#777'>last stop = time stationary (sent with F1 TV)</span>")


class RaceControl(Widget):
    """Race control messages and team radio clips, newest first, with the flag colour; weather on top."""
    title = 'RACE CONTROL'
    player = None  # one QMediaPlayer for every panel: a new clip stops the last

    def __init__(self, app, key, parent=None):
        super().__init__(app, key, parent)
        self.lay.removeWidget(self.body)
        self.body.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)  # its full height, for the scroll area
        self.body.linkActivated.connect(self.play)
        self.lay.addWidget(scrolled(self.body), 1)

    @classmethod
    def play(cls, url):
        """Plays a clip; clicking the one playing stops it."""
        from PySide6.QtCore import QUrl
        from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
        if cls.player is None:
            cls.player = QMediaPlayer()
            cls.player.setAudioOutput(QAudioOutput(cls.player))
        if cls.player.source() == QUrl(url) and cls.player.playbackState() == QMediaPlayer.PlayingState:
            cls.player.stop()
            return
        cls.player.setSource(QUrl(url))
        cls.player.play()

    def tick(self):
        if _need_feed(self):
            return
        st = self.app.state
        w = st.d.get('WeatherData', {})
        weather = (f"air {w.get('AirTemp', '?')}° · track {w.get('TrackTemp', '?')}° · humidity {w.get('Humidity', '?')}%"
                   f" · wind {w.get('WindSpeed', '?')} m/s · "
                   + ("<b style='color:#2f7cff'>RAIN</b>" if str(w.get('Rainfall')) == '1' else 'dry')) if w else ''
        msgs = [m for m in live.seq(st.d.get('RaceControlMessages', {}).get('Messages')) if isinstance(m, dict)]
        base = data.ARCHIVE + st.d.get('SessionInfo', {}).get('Path', '')
        dl = st.d.get('DriverList', {})
        for c in live.seq(st.d.get('TeamRadio', {}).get('Captures')):
            if isinstance(c, dict) and c.get('Path'):
                who = (dl.get(c.get('RacingNumber')) or {}).get('Tla') or c.get('RacingNumber', '?')
                msgs.append({'Utc': c.get('Utc', ''), 'Radio': base + c['Path'], 'Message': f'{who} radio'})
        msgs.sort(key=lambda m: m.get('Utc', ''))  # ISO times sort as text
        msgs = msgs[-60:]
        said = {m['Radio']: radio.text(m['Radio']) for m in msgs if m.get('Radio')} if radio.available() else {}  # asked oldest
        rows = []  # first: the newest clip is transcribed first
        for m in reversed(msgs):
            when = f"L{m['Lap']}" if m.get('Lap') else ''
            try:
                when += ' ' + datetime.fromisoformat(m['Utc']).replace(tzinfo=timezone.utc).astimezone().strftime('%H:%M')
            except (KeyError, ValueError):
                pass
            col = FLAG.get(m.get('Flag')) or ('#ffd400' if m.get('Category') == 'SafetyCar' else '#444444')
            text = html.escape(m.get('Message', ''))
            if m.get('Radio'):
                col, text = '#7cc4ff', f"<a href='{html.escape(m['Radio'])}' style='color:#7cc4ff; text-decoration:none'>▶ {text}</a>"
                if m['Radio'] in said:
                    t = said[m['Radio']]
                    text += ("<br><span style='color:#666'><i>transcribing…</i></span>" if t is None else
                             f"<br><span style='color:#bbb'>“{html.escape(t)}”</span>" if t else
                             "<br><span style='color:#666'><i>(nothing clear)</i></span>")
            rows.append([f"<span style='color:#888'>{when.strip()}</span>", col, text])
        self.body.setText(f"<span style='color:#999'>{weather}</span>" + (table(rows, self.docked) if rows else '<br><i>no messages yet</i>'))


def owner(app):
    """The tray app (main.App) behind a widget: the dashboard window passes itself as `app` to its panels."""
    return getattr(app, 'app', app)


def spark(values):
    """Values as a row of block characters, lowest ▁ to highest █."""
    if len(values) < 2:
        return ''
    lo, hi = min(values), max(values)
    return ''.join('▁▂▃▄▅▆▇█'[min(int((v - lo) / (hi - lo + 1e-9) * 8), 7)] for v in values)


class BattlePanel(Widget):
    """Races and sprints: my drivers against the cars just ahead and behind, plus pinned pairs (+ Battle, or right-click
    the timing tower). Each line: gap on track, gap once both have made the stops they still owe (pit loss), pace edge,
    when it catches, tyres; the gap over the last 10 laps; and the real rival (nearest once stops are counted)."""
    title = 'BATTLES'
    size_hint = (420, 520)

    def __init__(self, app, key, parent=None):
        super().__init__(app, key, parent)
        self.lay.removeWidget(self.body)
        self.body.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)  # its full height, for the scroll area
        self.body.linkActivated.connect(self.link)
        self.a, self.b = QComboBox(), QComboBox()
        add = QPushButton('+ Battle')
        add.clicked.connect(lambda: self.pin(self.a.currentText(), self.b.currentText()))
        row = QHBoxLayout()
        for w, stretch in ((self.a, 1), (QLabel('vs'), 0), (self.b, 1), (add, 0)):
            row.addWidget(w, stretch)
        self.lay.insertLayout(1, row)
        self.lay.addWidget(scrolled(self.body), 1)
        self.setStyleSheet(self.styleSheet() + ' QComboBox, QPushButton { font: 9pt "Segoe UI"; }')

    def pin(self, a, b):
        cfg = owner(self.app).cfg
        if a and b and a != b and [a, b] not in cfg['battles']:
            owner(self.app).set('battles', cfg['battles'] + [[a, b]])
            self.tick()

    def link(self, url):
        kind, a, b = url.split(':')
        if kind == 'pin':
            self.pin(a, b)
        else:
            owner(self.app).set('battles', [p for p in owner(self.app).cfg['battles'] if p != [a, b]])
            self.tick()

    def tick(self):
        if _need_feed(self):
            return
        st, s = self.app.state, self.app.state.session()
        ds = [d for d in st.drivers() if d['pos'] < 99]
        codes = [d['code'] for d in ds]
        for box in (self.a, self.b):
            if [box.itemText(i) for i in range(box.count())] != codes:
                keep = box.currentText()
                box.clear()
                box.addItems(codes)
                box.setCurrentText(keep)
        if not s['total'] or not s['lap']:
            self.body.setText('<i>Battles need a race or sprint under way.</i>')
            return
        app = owner(self.app)
        pinned = app.cfg['battles']
        out = {d['code'] for d in ds if d['out']}
        if any(set(p) & out for p in pinned):  # a pinned car retired: its card goes
            app.set('battles', pinned := [p for p in pinned if not set(p) & out])
        m = {**self.app.model(), 'two_compounds': 'Sprint' not in s['name']}
        loss, lap_s = st.pit_loss(m)['normal'], sim.lap_time(st.laps)
        by = {d['code']: d for d in ds}
        run = [d for d in ds if not d['out']]
        cards = [self.card(st, run, by[a], [by[b]], loss, m, lap_s, pinned=True) for a, b in pinned if a in by and b in by]
        for code in my_codes(self.app):
            me = by.get(code)
            if me and not me['out']:
                near = [d for d in run if abs(d['pos'] - me['pos']) == 1]
                cards.append(self.card(st, run, me, sorted(near, key=lambda d: d['pos']), loss, m, lap_s))
        self.body.setText(''.join(cards) or "<i>Pick My drivers in Settings, or pin a pair above "
                                             "(or right-click the timing tower).</i>")

    def card(self, st, ds, me, others, loss, m, lap_s, pinned=False):
        s = st.session()
        grey = "<span style='color:#888'>{}</span>".format
        tyre_of = lambda d: f"{tyre(d['cmp'], d['age'], st)} {grey(f'{n} stop' + ('s' if n != 1 else '') + ' to make' if (n := sim.stops_owed(d, s['total'] - s['lap'], m)) else 'no stop to make')}"
        head = (f"<b style='color:#{me['colour']}'>▌</b><b>{me['code']}</b> P{me['pos']} · {tyre_of(me)}"
                + (f" &nbsp;<a href='unpin:{me['code']}:{others[0]['code']}' style='color:#888;text-decoration:none'>✕</a>"
                   if pinned else ''))
        rows = []
        for o in others:
            x = sim.battle(ds, st.laps, me['drv'], o['drv'], s['lap'], s['total'], loss, m, lap_s)
            where = 'ahead' if x['gap'] < 0 else 'behind'
            net = x['net']
            net_txt = (f"{me['code'] if net > 0 else o['code']} ahead by {abs(net):.1f}s" + (
                f" ({o['code']} has {x['owed'][1]} stop{'s' if x['owed'][1] != 1 else ''} to make, {me['code']} {x['owed'][0]})"
                if x['owed'][0] != x['owed'][1] else ''))
            e, left = x['edge'], s['total'] - s['lap']
            pace = '' if e is None else f"{('closing' if e > 0 else 'dropping back') if where == 'ahead' else ('pulling away' if e > 0 else 'being caught')} {abs(e):.1f}s/lap"
            caught = int(np.ceil(x['gap'] / -e)) if e and e < 0 and where == 'behind' else None
            catch = (f" · catches in ~{x['catch']} laps" if x['catch'] else
                     f" · caught in ~{caught} laps" if caught and caught <= left else '')
            ot = " <b style='color:#3DDC84'>OVERTAKE MODE RANGE</b>" if x['close'] else ''
            rows.append(f"<br>{'▲' if where == 'ahead' else '▼'} <b style='color:#{o['colour']}'>{o['code']}</b> P{o['pos']}"
                        f" · on track <b>{abs(x['gap']):.1f}s {where}</b>{ot} · {tyre_of(o)}"
                        f"<br>&nbsp;&nbsp;&nbsp;{grey('net of stops:')} {net_txt}"
                        + (f"<br>&nbsp;&nbsp;&nbsp;{pace}{catch}" if pace else '')
                        + (f" &nbsp;<span style='color:#7cc4ff'>{spark([abs(v) for v in x['trend']])}</span> {grey('gap, last laps')}"
                           if len(x['trend']) >= 3 else ''))
        rival = sim.real_rival(ds, st.laps, me['drv'], s['lap'], s['total'], loss, m, lap_s)
        tail = ''
        if not pinned and rival and rival[0] not in {o['drv'] for o in others}:
            r = next(d for d in ds if d['drv'] == rival[0])
            tail = (f"<br>{grey('Real rival:')} <b style='color:#{r['colour']}'>{r['code']}</b> P{r['pos']}, "
                    f"{abs(rival[1]):.1f}s {'ahead' if rival[1] < 0 else 'behind'} net of stops "
                    f"<a href='pin:{me['code']}:{r['code']}' style='color:#7cc4ff'>pin</a>")
        return (f"<div style='margin-bottom:10px'>{head}{''.join(rows)}{tail}</div>"
                "<hr style='border:0;background:#252934;height:1px'>")


KINDS = {'drivers': DriverStandings, 'teams': ConstructorStandings, 'next': NextRace,
         'map': TrackMap, 'tower': TimingTower, 'sim': SimPanel, 'battles': BattlePanel}
