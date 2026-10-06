"""Desktop widgets: frameless, draggable, translucent windows. Data comes from data.py (static) and App.state (live)."""
import html
import threading
from datetime import datetime, timezone

import numpy as np
from PySide6.QtCore import QObject, QPointF, QRectF, QSizeF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import (QComboBox, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QPushButton, QSizeGrip,
                               QSizePolicy, QSpinBox, QVBoxLayout, QWidget)

from . import data, live, sim

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


def table(rows):
    """Rows of cells -> rich-text table. A cell starting with '#' is a colour bar."""
    out = ['<table cellspacing=0 cellpadding=2 width=100%>']
    for r in rows:
        out.append('<tr>' + ''.join(f'<td bgcolor="{c}" width=3></td>' if str(c).startswith('#') else f'<td>{c}</td>'
                                    for c in r) + '</tr>')
    return ''.join(out) + '</table>'


def r(s):
    return f'<div align=right>{s}</div>'


class Widget(QWidget):
    title = ''

    def __init__(self, app, key, parent=None):
        """parent=None: a free-floating desktop widget. With a parent it's a docked panel (live dashboard):
        no dragging, no resize grip, no saved position."""
        super().__init__(parent)
        self.app, self.key, self.cfg, self.docked = app, key, app.cfg['widgets'][key], parent is not None
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setStyleSheet('QLabel { color: #eee; font: 10pt "Segoe UI"; } QLabel#t { font: bold 10pt "Segoe UI"; color: #e10600; }')
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(12, 8, 12, 8)
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
        p.setPen(Qt.NoPen)
        # alpha >= 1: fully transparent pixels are click-through on Windows, and the widget couldn't be dragged
        p.setBrush(QColor(16, 16, 22, 225 if self.docked else max(1, round(255 * self.app.cfg['bg_opacity']))))
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
    """'Race' / 'Sprint' for the points-paying sessions, else None. Sprint Qualifying is Type 'Qualifying'."""
    if si.get('Type') != 'Race':
        return None
    return 'Sprint' if si.get('Name') == 'Sprint' else 'Race'


def projection(app):
    """Standings if the live race/sprint finished now: {'drivers': [...], 'teams': [...]} rows with 'points' and
    'delta', or None. F1's ChampionshipPrediction feed when it flows; otherwise Jolpica standings plus the points table
    for the running order, only while Jolpica doesn't have this session's results yet (else they'd count twice)."""
    st = app.state
    if not st or not isinstance(st.feed, live.LiveFeed):
        return None
    kind = session_kind(st.d.get('SessionInfo', {}))
    if not kind:
        return None
    pred = st.prediction()
    if pred:
        return pred
    drivers, teams, counted = (app.static.get(k) for k in ('drivers', 'teams', 'counted'))
    if (not isinstance(counted, dict) or counted.get(kind) or not isinstance(drivers, list)
            or st.session()['status'] not in ('Started', 'Finished', 'Finalised', 'Ends')):
        return None
    gain = {d['code']: POINTS[kind][d['pos'] - 1] for d in st.drivers() if d['pos'] <= len(POINTS[kind])}
    rows = [{**d, 'points': d['points'] + gain.get(d['code'], 0), 'delta': gain.get(d['code'], 0)} for d in drivers]
    team_gain = {}
    for d in rows:
        team_gain[d['team']] = team_gain.get(d['team'], 0) + d['delta']
    trows = [{**t, 'points': t['points'] + team_gain.get(t['name'], 0), 'delta': team_gain.get(t['name'], 0)}
             for t in (teams if isinstance(teams, list) else [])]
    return {'drivers': sorted(rows, key=lambda d: -d['points']), 'teams': sorted(trows, key=lambda t: -t['points'])}


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
        self.head.setText(self.title + (' · LIVE PROJECTION' if proj else ''))
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
        self.head.setText(self.title + (' · LIVE PROJECTION' if proj and proj['teams'] else ''))
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
        cd = ''
        if upcoming:
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
        tint = STATUS_TINT.get(str(st.d.get('TrackStatus', {}).get('Status')))
        if tint:
            p.setPen(QPen(QColor(tint), 3))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5), 10, 10)

        a = np.radians(st.track.rotation)  # same convention as FastF1's rotate()
        rot = np.array([[np.cos(a), np.sin(a)], [-np.sin(a), np.cos(a)]])
        pts = st.track.xy @ rot
        lo, span = pts.min(0), np.ptp(pts, 0) + 1e-9
        box = QRectF(self.rect()).adjusted(18, 40, -18, -28)
        sc = min(box.width() / span[0], box.height() / span[1])
        ox, oy = box.left() + (box.width() - span[0] * sc) / 2, box.bottom() - (box.height() - span[1] * sc) / 2

        def to(q):
            return QPointF(ox + (q[0] - lo[0]) * sc, oy - (q[1] - lo[1]) * sc)

        path = QPainterPath(to(pts[0]))
        for q in pts[1:]:
            path.lineTo(to(q))
        k = max(1.0, min(self.width(), self.height()) / 380)  # markers grow with the map (big in the live dashboard)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(90, 90, 100), 7 * k, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.drawPath(path)

        cars, gps = st.positions()
        dl = st.d.get('DriverList', {})
        order = {d['drv']: d['pos'] for d in st.drivers()}
        f = k * self.app.cfg.get('label_scale', 1.0)  # name size, live dashboard settings
        follow = self.app.cfg.get('follow')  # names shown only for these drivers (TLAs); None: everyone
        p.setFont(QFont('Segoe UI', max(5, round(7 * f)), QFont.Bold))
        cars = [(n, to(np.array([x, y]) @ rot), pit, dl.get(n) if isinstance(dl.get(n), dict) else {})
                for n, x, y, pit in sorted(cars, key=lambda c: order.get(c[0], 99))]
        for n, pt, pit, info in reversed(cars):  # leader's dot drawn last (on top)
            p.setPen(QPen(QColor(20, 20, 20), 1))
            p.setBrush(QColor(110, 110, 110) if pit else QColor(STATUS_TINT['4']) if n in live.SAFETY_CARS
                       else QColor('#' + info.get('TeamColour', '888888')))
            p.drawEllipse(pt, 5 * k, 5 * k)
        groups = []  # bunched cars: stack up to 4 labels in running order, then "+N"
        for n, pt, pit, info in cars:
            label = 'SC' if n in live.SAFETY_CARS else info.get('Tla') or n
            if follow is not None and label != 'SC' and label not in follow:
                continue
            g = next((g for g in groups if (g[0] - pt).manhattanLength() < 14 * f), None)
            if g is None:
                groups.append(g := [pt, []])
            g[1].append((label, pit))
        for pt, labels in groups:
            for i, (label, pit) in enumerate(labels[:4]):
                p.setPen(QColor(235, 235, 235) if not pit else QColor(140, 140, 140))
                p.drawText(pt + QPointF(7 * k, (4 + 10 * i) * f), label if i < 3 or len(labels) == 4 else f'+{len(labels) - 3}')
        w = st.d.get('WeatherData', {})
        if w.get('WindDirection') not in (None, ''):
            self.draw_wind(p, rot, float(w['WindDirection']), w.get('WindSpeed', '?'), k)

        p.setFont(QFont('Segoe UI', 8))
        p.setPen(QColor('#5f5') if gps else QColor('#fc5'))
        p.drawText(QRectF(self.rect()).adjusted(12, 0, -12, -8), Qt.AlignBottom | Qt.AlignLeft,
                   f"{'GPS' if gps else 'ESTIMATED'} · {getattr(st.feed, 'status', '')} · {st.session()['track']}")

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


class TimingTower(Widget):
    title = 'TIMING'
    size_hint = (330, 560)

    def tick(self):
        if _need_feed(self):
            return
        st, s = self.app.state, self.app.state.session()
        self.head.setText(f"{s['name'] or 'TIMING'}" + (f" · L{s['lap']}/{s['total']}" if s['total'] else ''))
        race = s['type'] in ('Race', 'Sprint')
        wide = self.docked and race  # the live dashboard has room for leader gap and best lap too
        grey = "<span style='color:#777'>{}</span>".format
        rows = [['', '', '', r(grey('INT')), r(grey('LEADER')), r(grey('LAST')), r(grey('BEST')), grey('TYRE'), grey('STOPS')]] if wide else []
        for d in st.drivers()[:self.cfg['n']]:
            letter, col = tyre_style(d['cmp'], st)
            gap = (d['int'] if d['pos'] > 1 else d['laps'] and f"L{d['laps']}") if race else d['best']
            rows.append([d['pos'], f"#{d['colour']}", f"<b>{d['code']}</b>" if not d['out'] else f"<s>{d['code']}</s>",
                         r(gap or ''), *([r(grey(d['gap'] if d['pos'] > 1 else ''))] if wide else []),
                         r(f"<span style='color:#999'>{d['last']}</span>"), *([r(grey(d['best']))] if wide else []),
                         f"<span style='color:{col}'><b>{letter}</b></span>{d['age']}",
                         'PIT' if d['pit'] else grey(d['stops'] or '')])
        status = getattr(st.feed, 'status', '')
        self.body.setText(f"<span style='color:#999'>{status} · {s['track']} {s['status']}</span>" + table(rows))


class SimPanel(Widget):
    title = 'STRATEGY SIM'
    size_hint = (400, 430)

    def __init__(self, app, key, parent=None):
        super().__init__(app, key, parent)
        self.drv, self.lap, self.cmp = QComboBox(), QSpinBox(minimum=1, maximum=99), QComboBox()
        self.cmp.addItems(['MEDIUM', 'HARD', 'SOFT', 'INTERMEDIATE', 'WET'])
        go, best = QPushButton('What if'), QPushButton('Best window')
        go.clicked.connect(lambda: self.run(False))
        best.clicked.connect(lambda: self.run(True))
        row = QHBoxLayout()
        pit = QLabel('pit on lap')
        pit.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)  # else it takes a share of the spare width
        for w, stretch in ((self.drv, 2), (pit, 0), (self.lap, 1), (self.cmp, 2)):
            row.addWidget(w, stretch)
        row2 = QHBoxLayout()
        row2.addWidget(go)
        row2.addWidget(best)
        self.lay.insertLayout(1, row)
        self.lay.insertLayout(2, row2)
        self.out = QLabel(wordWrap=True, textFormat=Qt.RichText, alignment=Qt.AlignTop | Qt.AlignLeft)
        self.lay.addWidget(self.out, 2)
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
            self.drv.setCurrentText(keep)
        if not s['total']:
            self.body.setText('<i>Strategy sim needs a race or sprint (lap count).</i>')
            return
        lap = s['lap'] or 1
        self.lap.setMinimum(lap)
        self.lap.setMaximum(max(lap, s['total'] - 1))
        me, loss = self.selected(), self.loss()
        src = loss['source'] + (f", {loss['n']} stops" if loss['n'] else '')
        under = {'4': 'sc', '6': 'vsc', '7': 'vsc'}.get(str(st.d.get('TrackStatus', {}).get('Status')))
        txt = (f"Lap {lap}/{s['total']} · est. car mass {sim.mass_kg(lap, s['total'], self.app.model()):.0f} kg"
               f" · pit loss {loss['normal']:.1f}s ({src})" + (f", {loss[under]:.1f}s under {under.upper()}" if under else '')
               + f" · {len(st.laps)} laps logged")
        if me and len(ds) > 1:
            pos, a, ga, b, gb = sim.rejoin(ds, me, loss[under or 'normal'])
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
        lap, pit_lap, cmp = s['lap'] or 1, self.lap.value(), self.cmp.currentText()
        meeting = st.d.get('SessionInfo', {}).get('Path', '').rsplit('/', 2)[0]
        self.out.setText('<i>simulating…' + ('' if meeting in self.priors else ' (first run loads this weekend\'s practice data)') + '</i>')

        def work():
            if meeting not in self.priors:
                fp = [p for label, p in data.past_sessions(int(meeting[:4] or 0) or None, 999)
                      if meeting and p.startswith(meeting) and 'Practice' in p]
                fp_laps = [{**l, 'sess': i} for i, p in enumerate(fp) for l in live.laps_from_archive(p, track=False)[1]]
                self.priors[meeting] = sim.priors_from(fp_laps, s['total'], m) if fp_laps else m['prior']
            model = sim.fit(laps, s['total'], {**m, 'prior': self.priors[meeting]})
            if window:
                return 'window', sim.best_window(ds, lap, model, me, pit_loss=loss)
            return 'whatif', (pit_lap, cmp, sim.what_if(ds, lap, model, me, pit_lap, cmp, pit_loss=loss))

        bg(work, self.show_result)

    def show_result(self, res):
        self.busy = False
        if isinstance(res, Exception):
            self.out.setText(f'<span style="color:#f66">sim failed: {res}</span>')
            return
        kind, x = res
        if kind == 'window':
            self.out.setText('<b>Best stop windows</b> (mean finish)' + table(
                [f'L{L}', f"<span style='color:{tyre_style(c, self.app.state)[1]}'>{c}</span>", r(f'P{mean:.2f}')] for mean, L, c in x[:8]))
            return

        def dist(a):
            v, n = np.unique(a, return_counts=True)
            top = sorted(zip(n, v), reverse=True)[:4]
            return ' '.join(f'P{int(p)} {c / len(a):.0%}' for c, p in top)
        pit_lap, cmp, x = x
        b, p = x['base'], x['plan']
        delta = b.mean() - p.mean()
        self.out.setText(table([
            ['', r('mean'), 'most likely finishes'],
            ['Current plan', r(f'P{b.mean():.2f}'), dist(b)],
            [f'Pit L{pit_lap} {cmp}', r(f'P{p.mean():.2f}'), dist(p)],
        ]) + f"<br><b style='color:{'#5f5' if delta > 0.05 else '#f66' if delta < -0.05 else '#ccc'}'>"
             f"{'gains' if delta >= 0 else 'loses'} {abs(delta):.2f} places on average</b>"
             f"<br><span style='color:#777'>Model: fitted lap times, tyre deg, fuel burn, SC risk. Not modelled: ERS, tyre temps, team intent.</span>")


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
    return [x for x in live.seq(st.d.get('TimingAppData', {}).get('Lines', {}).get(drv, {}).get('Stints')) if x]


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
            if q:
                best = (seq_at(ln.get('BestLapTimes'), q['part'] - 1) or {}).get('Value', '')
                gap = (seq_at(ln.get('Stats'), q['part'] - 1) or {}).get('TimeDiffToFastest', '')
            else:
                best, gap = (ln.get('BestLapTime') or {}).get('Value', ''), ln.get('TimeDiffToFastest', '')
            ko = ln.get('KnockedOut')
            pos = d['pos'] if d['pos'] < 99 else ''
            if q and q['safe'] and not ko and pos and pos > q['safe']:
                pos = f"<span style='color:#ff4040'><b>{pos}</b></span>"  # knocked out if the part ended now
            code = f"<b>{d['code']}</b>" if not (ko or d['out']) else grey(d['code'])
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
        self.body.setText(f"<span style='color:#999'>{getattr(st.feed, 'status', '')} · {s['track']} {s['status']}</span>"
                          f"<div style='font-size:9pt'>{table(rows)}</div>")  # 9pt: 22 rows fit


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
                          f"<div style='font-size:9pt'>{table(rows)}</div>")


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
        rows = [['', '', '', grey('STINTS: compound + laps on the set, * = used set'), r(grey('STOPS')), r(grey('LAST STOP'))]]
        for d in ds:
            cells, used_pct = [], 0.0
            for x in stints(st, d['drv']):
                n = max(int(x.get('TotalLaps') or 0) - int(x.get('StartLaps') or 0), 0)  # TotalLaps = tyre age
                if not n:
                    continue
                letter, col = tyre_style(x.get('Compound'), st)
                pct = 100 * n / total
                used_pct += pct
                cells.append(f"<td width='{pct:.1f}%' bgcolor='{col}' align=center><span style='color:#000;font-size:7pt'>"
                             f"<b>{letter}</b>{n}{'' if str(x.get('New')).lower() == 'true' else '*'}</span></td>")
            if used_pct < 99.5:
                cells.append(f"<td width='{100 - used_pct:.1f}%'></td>")
            bar = "<table width=100% cellspacing=1 cellpadding=0><tr>" + ''.join(cells) + "</tr></table>"
            mine = stops.get(d['drv'], [])
            last = mine[-1].get('stationary') if mine else None
            rows.append([d['pos'] if d['pos'] < 99 else '', f"#{d['colour']}",
                         f"<b>{d['code']}</b>" if not d['out'] else f"<s>{d['code']}</s>", bar,
                         r(d['stops'] or ''), r(grey(f'{last:.1f}s' if last else ''))])
        self.body.setText(f"<div style='font-size:9pt'>{table(rows)}</div>"
                          "<span style='color:#777'>last stop = time stationary (sent with F1 TV)</span>")


class RaceControl(Widget):
    """Race control messages, newest first, with the flag colour; weather on top."""
    title = 'RACE CONTROL'

    def tick(self):
        if _need_feed(self):
            return
        st = self.app.state
        w = st.d.get('WeatherData', {})
        weather = (f"air {w.get('AirTemp', '?')}° · track {w.get('TrackTemp', '?')}° · humidity {w.get('Humidity', '?')}%"
                   f" · wind {w.get('WindSpeed', '?')} m/s · "
                   + ("<b style='color:#2f7cff'>RAIN</b>" if str(w.get('Rainfall')) == '1' else 'dry')) if w else ''
        msgs = [m for m in live.seq(st.d.get('RaceControlMessages', {}).get('Messages')) if isinstance(m, dict)]
        rows = []
        for m in reversed(msgs[-40:]):
            when = f"L{m['Lap']}" if m.get('Lap') else ''
            try:
                when += ' ' + datetime.fromisoformat(m['Utc']).replace(tzinfo=timezone.utc).astimezone().strftime('%H:%M')
            except (KeyError, ValueError):
                pass
            col = FLAG.get(m.get('Flag')) or ('#ffd400' if m.get('Category') == 'SafetyCar' else '#444444')
            rows.append([f"<span style='color:#888'>{when.strip()}</span>", col, html.escape(m.get('Message', ''))])
        self.body.setText(f"<span style='color:#999'>{weather}</span>" + (table(rows) if rows else '<br><i>no messages yet</i>'))


KINDS = {'drivers': DriverStandings, 'teams': ConstructorStandings, 'next': NextRace,
         'map': TrackMap, 'tower': TimingTower, 'sim': SimPanel}
