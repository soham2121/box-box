"""Offscreen end-to-end check: replays a race through the real App and widgets, prints what the widgets show.
Run: .venv\\Scripts\\python scripts\\smoke_replay.py [archive path] [lap,lap,...]"""
import os
import re
import sys
import time
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding='utf-8')

from PySide6.QtWidgets import QApplication  # noqa: E402

from boxbox import live, main  # noqa: E402

PATH = sys.argv[1] if len(sys.argv) > 1 else '2026/2026-10-04_Bahrain_Grand_Prix/2026-10-04_Race/'
LAPS = [int(x) for x in (sys.argv[2] if len(sys.argv) > 2 else '11,25').split(',')]


def text(w):
    return ' '.join(re.sub('<[^>]+>', ' ', w.body.text()).split())


def wait(qa, cond, limit):
    t = time.time()
    while not cond() and time.time() - t < limit:
        qa.processEvents()
        time.sleep(0.05)
    return cond()


qa = QApplication([])
qa.setQuitOnLastWindowClosed(False)
main.App.save = lambda self: None  # never touch the user's config.json
app = main.App(qa)
for k in main.KINDS:
    app.show(k)
assert wait(qa, lambda: isinstance(app.static.get('drivers'), list), 60), 'standings did not load'
print('drivers  ::', text(app.windows['drivers'])[:160])
print('teams    ::', text(app.windows['teams'])[:160])
print('next     ::', text(app.windows['next'])[:160])

app.start(live.ReplayFeed, PATH, 60.0)
st = app.state
for lap in LAPS:
    assert wait(qa, lambda: st.session()['lap'] >= lap, 600), f'replay never reached lap {lap}'
    st.feed.set_speed(0.001)  # hold here while checking
    app.tick()
    s, loss = st.session(), st.pit_loss(app.model())
    cars, gps = st.positions()
    print(f"\n== {s['name']} lap {s['lap']}/{s['total']} · {s['track']} · status {st.feed.status}")
    print(f"pit loss {loss['normal']:.1f}s ({loss['source']}, n={loss['n']}) sc {loss['sc']:.1f} vsc {loss['vsc']:.1f}")
    print('map      ::', len(cars), 'cars,', 'GPS' if gps else 'estimated', '· track', bool(st.track))
    print('tower    ::', text(app.windows['tower'])[:200])
    w = app.windows['sim']
    w.tick()
    print('sim      ::', text(w))
    w.drv.setCurrentIndex(3)
    w.lap.setValue(s['lap'] + 3)
    w.cmp.setCurrentText('HARD')
    w.run(False)
    assert wait(qa, lambda: not w.busy, 600), 'what-if did not finish'
    print('what-if  ::', w.drv.currentText(), '·', ' '.join(re.sub('<[^>]+>', ' ', w.out.text()).split())[:260])
    pred = st.prediction()
    if pred:
        print('F1 projection (feed) ::', ', '.join(f"{d['code']} {d['points']:g} (+{d['delta']:g})" for d in pred['drivers'][:5]))
    st.feed.set_speed(60.0)
app.windows['map'].grab().save(str(Path(os.environ.get('TEMP', '.')) / 'f1_smoke_map.png'))
print('\nsmoke OK')
