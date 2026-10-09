"""Offscreen check of the live dashboard: connects live on launch, then replays Bahrain to lap 25 and runs a what-if.
Run: .venv\Scripts\python scripts\smoke_dashboard.py"""
import os, re, sys, time, tempfile, threading
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtWidgets import QApplication
from f1tracker import dashboard, live, widgets
dashboard.SETTINGS = Path(tempfile.mkdtemp()) / 'dashboard.json'
qa = QApplication([])
d = dashboard.Dashboard(); d.show()
def wait(cond, limit):
    t = time.time()
    while not cond() and time.time() - t < limit: qa.processEvents(); time.sleep(0.05)
    return cond()
text = lambda w: ' '.join(re.sub('<[^>]+>', ' ', w.body.text()).split())
wait(lambda: False, 20)
print('live on launch:', type(d.state.feed).__name__, '|', d.status_text())
print('signed in:', d.signed_in, '| sign-in button:', d.signin.text())
first = d.state.feed
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
print('old live feed stop flag:', first.stop)
print('status ::', d.status_text())
print('tower  ::', text(d.tower)[:330])
print('sim    ::', text(d.sim))
d.sim.drv.setCurrentIndex(2); d.sim.lap.setValue(d.state.session()['lap'] + 2); d.sim.run(False)
wait(lambda: not d.sim.busy, 300)
print('whatif ::', ' '.join(re.sub('<[^>]+>', ' ', d.sim.out.text()).split())[:200])
d.grab().save(os.environ['TEMP'] + '/f1_dash.png')
d.close(); wait(lambda: False, 1)
print('settings saved:', dashboard.SETTINGS.exists(), '| live threads alive:', sum(isinstance(t, live.LiveFeed) and t.is_alive() for t in threading.enumerate()))
