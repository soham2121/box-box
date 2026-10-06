# F1 tracker

Windows desktop widgets for Formula 1: championship standings, next race countdown, a live track map and timing tower, and a strategy simulator for pit stop what-ifs.

## Setup

Requires Python 3.11+ on Windows.

```
git clone https://github.com/soham2121/f1-thing.git
cd f1-thing
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python -m f1tracker.main
```

After setup, create desktop shortcuts **F1 Live** (dashboard) and **F1 Tracker** (tray widgets) with the F1 icon:

```
powershell -ExecutionPolicy Bypass -File scripts\create_shortcuts.ps1
```

(Run it again if you move the project folder.) Without shortcuts: double-click `F1 Tracker.bat` / `F1 Live.bat`, or run `.venv\Scripts\pythonw.exe run.pyw`.

The app starts as a tray icon (red **F1**). Right-click it for everything else. Tray → **Start with Windows** makes it start at login (per-user, no admin). Widgets come back where you left them: positions are saved a second after every move or resize.

## Widgets

| Widget | Shows |
|---|---|
| Driver standings | Top N drivers; during a live race/sprint, the standings if it finished now (F1's own projection feed, so sprint points never count twice) |
| Constructor standings | Top N teams; same live projection |
| Next race | Weekend sessions in your local time, countdown to the next one |
| Track map | Car positions, safety car, SC/VSC/red flag border |
| Timing tower | Position, interval, last lap, tyre + age, stops, pit status |
| Strategy sim | Pit rejoin position, "pit on lap N for compound C" vs current plan, best stop window |

Drag widgets to move them, use the corner grip to resize. Tray menu: toggle widgets, rows shown, text opacity and background opacity (separately; background 0% still keeps the widget draggable), lock positions, always-on-top vs desktop layer, refresh interval.

On the desktop layer the widgets sit behind your apps but stay visible after **Win+D** / Show desktop, the way Rainmeter does it: when the desktop is in front they're lifted above it. Only one tray app runs at a time: launching it again just shows a notification pointing at the tray icon.

## Live dashboard

One window for the whole weekend. No championship standings. The track map with the wind compass is always on the left. The right side changes with the session type (also in replays):

| | Top right | Bottom right |
|---|---|---|
| **Race / sprint** | tabs: **Timing** · **Tyre stints** | tabs: **Strategy sim** · **Race control** |
| **Practice / qualifying** | **sector timing** | **Tyres** and **Race control** side by side |

- **Sector timing:**
  - Best lap and gap to P1.
  - The current lap's S1/S2/S3: mini-sector squares (yellow, green = personal best, purple = overall best, grey = pit lane), and each sector time once done.
  - Last lap, tyre, laps.
  - Qualifying: the header shows Q1/Q2/Q3 (SQ in sprint qualifying) and the time left. Best and gap are for the current part, drop-zone positions are red, knocked-out drivers are greyed.
- **Tyre stints:** each driver's sets as coloured bars across the race distance (`M24` = medium for 24 laps, `*` = used set), stops, and the last stop's stationary time (with F1 TV).
- **Tyres:** current compound and its age, new or used, sets used so far.
- **Race control:** messages newest first with lap, time and flag colour; weather on top.

Tab choices and splitter sizes are remembered.

Start it by double-clicking **`F1 Live.bat`**, or with tray → **Open live dashboard**. It connects to the live feed straight away.

Toolbar:
- **● Go live**: reconnect to the live feed.
- **Replay**: pick a year (2018–now), then the weekend, then the session; each opens to the side. A year's list loads the first time you open it (a second or two). F1's archive refuses most of 2022, so those files come from FastF1's mirror of the archive (`livetiming-mirror.fastf1.dev`, the same fallback FastF1 uses). Nothing before 2018 exists on either. Some 2018 races (e.g. Australia) have no GPS, so the map shows estimated positions there. 2018 tyres use Pirelli's 2018 range and colours: HS hypersoft pink, US ultrasoft purple, SS supersoft red, S soft yellow, M medium white, H hard ice blue, SH superhard orange. The sim is tuned for the 2019+ soft/medium/hard tyres, so treat it as rough for 2018. A second toolbar row then appears: **Pause/Play**, **◀ Lap / Lap ▶** (±1 min outside races), a timeline slider to jump anywhere, the current lap and time, and speed. Jumps take under a second, and the lap history behind them is rebuilt so the sim sees the race as it was at that point. Tray replays have **Jump to lap…** and **Pause** in the menu.
- **■ Stop**: disconnect.
- **Settings…** (changes apply at once, saved in `dashboard.json`):
  - **Driver names on the map**: 50–300 %.
  - **Delay live data**: live timing runs ahead of the F1 TV video stream. Set this (in seconds) so the map and timing match what you see on the stream. Live only, replays ignore it. The status line shows `delayed N s`.
  - **Replays start at**: default replay speed (1x–60x; F1 Live only, tray replays start at 10x).
  - **Show names for**: tick the drivers to follow. Every car keeps its dot on the map; only ticked drivers get a name. **All** shows everyone, including new drivers.

The map also shows:
- **Cars out of the session** leave the map (F1's `Stopped` flag). A car that gets going again comes back.
- **Safety car**: a yellow `SC` dot, only while it's out on track. F1 sends it as car 241, 242 or 243 (it varies by race).
- **Wind compass** (top right): `N` turned with the map, plus an arrow pointing where the wind blows, and `wind 1.8 m/s from E`.
- **Sign in to F1 TV…**: opens FastF1's login page. It needs the **FastF1 Companion** browser extension (Chrome/Edge/Brave/Firefox; the page links it). Log in at formula1.com, then click **Connect FastF1** on the page the extension opens. The app waits up to 30 min, then reconnects with your account (real GPS positions) and keeps the laps logged so far.

The window remembers its size and splitter positions (`%APPDATA%1tracker\dashboard.json`). It runs separately from the tray widgets, so you can close the standings during a race. Only one dashboard runs at a time: launching it again brings the open window to the front.

What the live feed sends:

| Data | Without F1 TV | With F1 TV |
|---|---|---|
| Timing, gaps, laps, tyres, track status, weather, race control | yes | yes |
| Car GPS positions (+ car telemetry) | no: map estimated from timing | yes |
| F1's championship projection | no: computed from the running order + Jolpica | yes |
| Pit-stop times (`PitStopSeries`) | no: stops taken from the timing tower's pit flags | not in the post-session snapshot; to be checked during a live session |

Checked 2026-10-06 against F1's server between sessions, with and without an F1 TV login.

## Live sessions (tray widgets)

Tray → **Live tracker** during any session.

- **Without F1 TV:** timing, tyres and gaps are live; car positions on the map are estimated from timing (typically within 100–200 m).
- **With F1 TV:** tray → **Sign in to F1 TV…**, log in in the browser, then restart the live tracker for real GPS positions.

Tray → **Replay a past session** (year → weekend → session, same menu as F1 Live) plays any finished session from 2018 on through the same widgets (speed adjustable), so you can try everything between race weekends.

## Strategy sim knobs

Model settings live in `%APPDATA%\f1tracker\config.json` under `"model"` (tray → **Edit sim model knobs**), e.g.:

```json
"model": {"fuel_start_kg": 70, "pass_threshold": 1.0, "sc_prob": 0.4}
```

Restart the app after editing. Defaults and their meanings are in `MODEL` at the top of `f1tracker/sim.py`.

### Pit loss

The sim panel shows where its pit loss comes from, in this order:

1. **circuit data**: MultiViewer's value for the circuit (normal / SC / VSC)
2. **this session**: measured from at least 3 green-flag stops in the running race (in-lap + out-lap against the driver's own laps either side). Laps behind the SC/VSC, red-flag restarts and SC starts are excluded
3. **sprint**: measured the same way in this weekend's sprint
4. **typical circuit**: median over all circuits MultiViewer knows
5. **default**: `pit_loss` in `MODEL`

While the SC or VSC is out, "pit now" uses the cheaper SC/VSC pit loss.

## Tests

```
.venv\Scripts\pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest                    # unit tests, offline
.venv\Scripts\python -m pytest -m integration     # downloads real archive data
.venv\Scripts\python -m f1tracker.sim             # backtest the lap-time model on a past race
.venv\Scripts\python scripts\smoke_replay.py      # offscreen replay through the real widgets
```

Manual test steps: [docs/TESTING.md](docs/TESTING.md). Live weekend checklist: [docs/SINGAPORE_LIVE_TEST.md](docs/SINGAPORE_LIVE_TEST.md). Progress: [docs/CHECKLIST.md](docs/CHECKLIST.md).

## Data sources

- [Jolpica-F1](https://github.com/jolpica/jolpica-f1): standings and schedule
- F1 live timing (via [FastF1](https://github.com/theOehrly/Fast-F1)): live data and the session archive
- [MultiViewer](https://multiviewer.app) circuit API: track outlines and pit loss

Not affiliated with Formula 1. Responses are cached in `%APPDATA%\f1tracker\cache`.
