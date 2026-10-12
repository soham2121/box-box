"""Offscreen check of the app with its dashboard (temp config, the real one is untouched): goes live by hand, replays
Bahrain to lap 25 while live keeps recording, runs a what-if, closes the dashboard (tray and live carry on).
Run: .venv\Scripts\python scripts\smoke_dashboard.py"""
import os, re, sys, time, tempfile, threading
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtWidgets import QApplication
from lightsout import live, main, widgets
tmp = Path(tempfile.mkdtemp())
main.CONFIG, main.DASH_SETTINGS = tmp / 'config.json', tmp / 'dashboard.json'
from lightsout import dashboard
dashboard.SETTINGS = main.DASH_SETTINGS
qa = QApplication([])
app = main.App(qa)
app.open_dashboard()
d = app.dash
def wait(cond, limit):
    t = time.time()
    while not cond() and time.time() - t < limit: qa.processEvents(); time.sleep(0.05)
    return cond()
text = lambda w: ' '.join(re.sub('<[^>]+>', ' ', w.body.text()).split())
d.go_live()
wait(lambda: False, 8)
print('live:', type(app.live.feed).__name__, '| shown:', app.state is app.live, '|', d.status_text())
print('tray:', app.tray.toolTip().replace('\n', ' | '))
d.build_replay_menu()
years = {a.text(): a.menu() for a in d.replay_menu.actions() if a.menu()}
years['2024'].aboutToShow.emit()  # starts loading the year in the background
assert wait(lambda: widgets.REPLAYS.get(2024), 60), 'replay year did not load'
years['2024'].aboutToShow.emit()  # a hidden menu is not refilled in place
print('replay menu years:', ', '.join(years), '| 2024 weekends:', len(years['2024'].actions()))
d.start_replay('2026/2026-10-04_Bahrain_Grand_Prix/2026-10-04_Race/')
d.state.feed.set_speed(60.0)
assert wait(lambda: d.state.session()['lap'] >= 25, 600)
d.state.feed.set_speed(0.001); wait(lambda: False, 1.5)
print('replay shown, live kept:', app.state is not app.live and not app.live.feed.stop)
print('status ::', d.status_text(), '| button:', d.live_btn.text())
print('tower  ::', text(d.tower)[:330])
print('sim    ::', text(d.sim))
d.sim.drv.setCurrentIndex(2); d.sim.lap.setValue(d.state.session()['lap'] + 2); d.sim.run(False)
wait(lambda: not d.sim.busy, 300)
print('whatif ::', ' '.join(re.sub('<[^>]+>', ' ', d.sim.out.text()).split())[:200])
d.grab().save(os.environ['TEMP'] + '/f1_dash.png')
d.go_live()  # back to live: the replay stops
print('back to live:', app.state is app.live)
d.close(); wait(lambda: False, 1)
print('dashboard closed:', app.dash is None, '| layout saved:', main.DASH_SETTINGS.exists(),
      '| live threads alive:', sum(isinstance(t, live.LiveFeed) and t.is_alive() for t in threading.enumerate()))
app.stop_feed()
print('stopped:', app.live is None, '| paused till session over:', app.live_paused)
