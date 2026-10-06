"""Desktop widgets: frameless, draggable, translucent windows. Data comes from data.py (static) and App.state (live)."""
import threading
from datetime import datetime, timezone

import numpy as np
from PySide6.QtCore import QObject, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QComboBox, QHBoxLayout, QLabel, QPushButton, QSizeGrip, QSpinBox, QVBoxLayout,
                               QWidget)

from . import data, live, sim

TYRE = {'SOFT': ('S', '#ff3333'), 'MEDIUM': ('M', '#ffd400'), 'HARD': ('H', '#f0f0f0'),
        'INTERMEDIATE': ('I', '#39b54a'), 'WET': ('W', '#2f7cff')}
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

    def __init__(self, app, key):
        super().__init__(None)
        self.app, self.key, self.cfg = app, key, app.cfg['widgets'][key]
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setStyleSheet('QLabel { color: #eee; font: 10pt "Segoe UI"; } QLabel#t { font: bold 10pt "Segoe UI"; color: #e10600; }')
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(12, 8, 12, 8)
        head = QHBoxLayout()
        self.head = QLabel(self.title, objectName='t')
        head.addWidget(self.head, 1)
        head.addWidget(QSizeGrip(self), 0, Qt.AlignTop | Qt.AlignRight)
        self.lay.addLayout(head)
        self.body = QLabel(wordWrap=True, textFormat=Qt.RichText, alignment=Qt.AlignTop | Qt.AlignLeft)
        self.lay.addWidget(self.body, 1)
        self.drag = None
        g = self.cfg.get('geom')
        self.setGeometry(*(g or (60 + 40 * list(app.cfg['widgets']).index(key), 80 + 40 * list(app.cfg['widgets']).index(key), *self.size_hint)))
        self.apply_flags()

    size_hint = (260, 320)

    def apply_flags(self):
        layer = Qt.WindowStaysOnTopHint if self.app.cfg['on_top'] else Qt.WindowStaysOnBottomHint
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool | layer)  # ponytail: Win+D still hides these; pin via on-top
        self.setWindowOpacity(self.app.cfg['opacity'])

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(16, 16, 22, 225))
        p.drawRoundedRect(self.rect(), 10, 10)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and not self.app.cfg['locked']:
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
        g = self.geometry()
        self.cfg['geom'] = [g.x(), g.y(), g.width(), g.height()]

    def refresh(self):
        """Static data changed (standings/schedule refetched)."""

    def tick(self):
        """Live state changed (called every ~0.5 s while a feed runs)."""


# ---- static widgets ------------------------------------------------------------------------------------------------

class DriverStandings(Widget):
    title = 'DRIVERS'

    def refresh(self):
        rows = self.app.static.get('drivers')
        if isinstance(rows, Exception) or rows is None:
            self.body.setText(f'<i>{rows or "loading…"}</i>')
            return
        proj = self.projected()
        if proj:
            rows = sorted(rows, key=lambda d: -(d['points'] + proj.get(d['code'], 0)))
        self.head.setText(self.title + (' · LIVE PROJECTION' if proj else ''))
        self.body.setText(table(
            [i, f"#{d['colour']}", f"<b>{d['code']}</b>", f"<span style='color:#999'>{d['team']}</span>",
             r(f"{d['points'] + proj.get(d['code'], 0):g}" + (f" <span style='color:#5f5'>+{proj[d['code']]}</span>" if proj.get(d['code']) else ''))]
            for i, d in enumerate(rows[:self.cfg['n']], 1)))

    def projected(self):
        """Points each driver would score if the live race/sprint finished now (live feed only, not replays)."""
        st = self.app.state
        if not st or not isinstance(st.feed, live.LiveFeed):
            return {}
        s, si = st.session(), st.d.get('SessionInfo', {})
        table_ = POINTS.get('Sprint' if 'Sprint' in si.get('Name', '') else si.get('Type'))
        nxt, done_round = self.app.static.get('next'), self.app.static.get('round', 0)
        if not table_ or s['status'] not in ('Started', 'Finished', 'Finalised', 'Ends') or not nxt or nxt['round'] <= done_round:
            return {}  # ponytail: after a sprint Jolpica's round already equals this weekend, so race projection hides
        return {d['code']: table_[d['pos'] - 1] for d in st.drivers() if d['pos'] <= len(table_)}

    tick = refresh


class ConstructorStandings(Widget):
    title = 'CONSTRUCTORS'

    def refresh(self):
        rows = self.app.static.get('teams')
        if isinstance(rows, Exception) or rows is None:
            self.body.setText(f'<i>{rows or "loading…"}</i>')
            return
        self.body.setText(table([d['pos'], f"#{d['colour']}", d['name'], r(f"{d['points']:g}")] for d in rows[:self.cfg['n']]))


class NextRace(Widget):
    title = 'NEXT RACE'
    size_hint = (280, 230)

    def __init__(self, app, key):
        super().__init__(app, key)
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
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(90, 90, 100), 7, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.drawPath(path)

        cars, gps = st.positions()
        dl = st.d.get('DriverList', {})
        order = {d['drv']: d['pos'] for d in st.drivers()}
        p.setFont(QFont('Segoe UI', 7, QFont.Bold))
        drawn = []
        for n, x, y, pit in sorted(cars, key=lambda c: -order.get(c[0], 99)):  # leader drawn last (on top)
            info = dl.get(n) if isinstance(dl.get(n), dict) else {}
            pt = to(np.array([x, y]) @ rot)
            stack = sum(1 for q in drawn if (q - pt).manhattanLength() < 8)  # bunched cars: stack labels, not overprint
            drawn.append(pt)
            col = QColor('#' + info.get('TeamColour', '888888'))
            p.setPen(QPen(QColor(20, 20, 20), 1))
            p.setBrush(QColor(110, 110, 110) if pit else col)
            p.drawEllipse(pt, 5, 5)
            p.setPen(QColor(235, 235, 235) if not pit else QColor(140, 140, 140))
            p.drawText(pt + QPointF(7, 4 + 10 * stack), info.get('Tla') or {'241': 'SC', '242': 'MED'}.get(n, n))

        p.setFont(QFont('Segoe UI', 8))
        p.setPen(QColor('#5f5') if gps else QColor('#fc5'))
        p.drawText(QRectF(self.rect()).adjusted(12, 0, -12, -8), Qt.AlignBottom | Qt.AlignLeft,
                   f"{'GPS' if gps else 'ESTIMATED'} · {getattr(st.feed, 'status', '')} · {st.session()['track']}")


class TimingTower(Widget):
    title = 'TIMING'
    size_hint = (330, 560)

    def tick(self):
        if _need_feed(self):
            return
        st, s = self.app.state, self.app.state.session()
        self.head.setText(f"{s['name'] or 'TIMING'}" + (f" · L{s['lap']}/{s['total']}" if s['total'] else ''))
        race = s['type'] in ('Race', 'Sprint')
        rows = []
        for d in st.drivers()[:self.cfg['n']]:
            letter, col = TYRE.get(d['cmp'], ('?', '#888'))
            gap = (d['int'] if d['pos'] > 1 else d['laps'] and f"L{d['laps']}") if race else d['best']
            rows.append([d['pos'], f"#{d['colour']}", f"<b>{d['code']}</b>" if not d['out'] else f"<s>{d['code']}</s>",
                         r(gap or ''), r(f"<span style='color:#999'>{d['last']}</span>"),
                         f"<span style='color:{col}'><b>{letter}</b></span>{d['age']}",
                         'PIT' if d['pit'] else f"<span style='color:#777'>{d['stops'] or ''}</span>"])
        status = getattr(st.feed, 'status', '')
        self.body.setText(f"<span style='color:#999'>{status} · {s['track']} {s['status']}</span>" + table(rows))


class SimPanel(Widget):
    title = 'STRATEGY SIM'
    size_hint = (400, 430)

    def __init__(self, app, key):
        super().__init__(app, key)
        self.drv, self.lap, self.cmp = QComboBox(), QSpinBox(minimum=1, maximum=99), QComboBox()
        self.cmp.addItems(['MEDIUM', 'HARD', 'SOFT', 'INTERMEDIATE', 'WET'])
        go, best = QPushButton('What if'), QPushButton('Best window')
        go.clicked.connect(lambda: self.run(False))
        best.clicked.connect(lambda: self.run(True))
        row = QHBoxLayout()
        for w in (self.drv, QLabel('pit L'), self.lap, self.cmp):
            row.addWidget(w)
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
        me = self.selected()
        txt = (f"Lap {lap}/{s['total']} · est. car mass {sim.mass_kg(lap, s['total'], self.app.model()):.0f} kg"
               f" · pit loss {self.loss()['normal']:.1f}s · {len(st.laps)} laps logged")
        if me and len(ds) > 1:
            pos, a, ga, b, gb = sim.rejoin(ds, me, self.loss()['normal'])
            txt += (f"<br>Pit <b>now</b> → rejoin <b>P{pos}</b>" + (f", {ga:.1f}s behind {a}" if a else '')
                    + (f", {gb:.1f}s ahead of {b}" if b else ''))
        self.body.setText(txt)

    def selected(self):
        t = self.drv.currentText()
        return t.split()[-1] if t else None

    def loss(self):
        st = self.app.state
        return (st.track.pit_loss if st.track else None) or {'normal': sim.pit_loss_from(st.laps, self.app.model()['pit_loss'])}

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
                fp_laps = [l for p in fp for l in live.laps_from_archive(p, track=False)[1]]
                self.priors[meeting] = sim.priors_from(fp_laps, m) if fp_laps else m['prior']
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
                [f'L{L}', f"<span style='color:{TYRE.get(c, ('', '#ccc'))[1]}'>{c}</span>", r(f'P{mean:.2f}')] for mean, L, c in x[:8]))
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


KINDS = {'drivers': DriverStandings, 'teams': ConstructorStandings, 'next': NextRace,
         'map': TrackMap, 'tower': TimingTower, 'sim': SimPanel}
