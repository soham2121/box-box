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
| Driver standings | Top N drivers; during a live or replayed race/sprint, the standings if it finished now (F1's own projection feed, so sprint points never count twice) |
| Constructor standings | Top N teams; same projection |
| Next race | Weekend sessions in your local time, countdown to the next one |
| Track map | Car positions, safety car, SC/VSC/red flag border |
| Timing tower | Position, interval, last lap, tyre + age, stops, pit status |
| Strategy sim | Pit rejoin position, "pit on lap N for compound C" vs current plan, best stop window, "what if it rains / dries from lap N", "what if a safety car / VSC / red flag comes out on lap N" |

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
- **Replay**: pick a year (2018–now), then the weekend, then the session; each opens to the side. A year's list loads the first time you open it (a second or two). F1's archive refuses most of 2022, so those files come from FastF1's mirror of the archive (`livetiming-mirror.fastf1.dev`, the same fallback FastF1 uses). Nothing before 2018 exists on either. Some 2018 races (e.g. Australia) have no GPS, so the map shows estimated positions there. 2018 tyres use Pirelli's 2018 range and colours: HS hypersoft pink, US ultrasoft purple, SS supersoft red, S soft yellow, M medium white, H hard ice blue, SH superhard orange. For the sim, a 2018 race's compounds become its softest/middle/hardest (SOFT/MEDIUM/HARD), as the feed names them from 2019. If only two were run they become SOFT/MEDIUM, a guess since the third is unknown.
- **Standings during replays**: the tray's driver and constructor standings show the projection as of that replay (`PROJECTION AS OF 2019 REPLAY`). From mid-2019 that's F1's own projection feed. Before that it's computed from the standings before the session (Jolpica) plus that season's points: race top 10 25–1, +1 for the fastest lap in the top 10 from 2019 to 2024, sprint 3-2-1 in 2021 and 8–1 from 2022.
- **Qualifying in the tray timing tower**: Q1/Q2/Q3 (SQ for sprint qualifying) and time left in the header, best lap of the current part, drop zone in red, knocked-out cars greyed. A second toolbar row then appears: **Pause/Play**, **◀ Lap / Lap ▶** (±1 min outside races), a timeline slider to jump anywhere, the current lap and time, and speed. Jumps take under a second, and the lap history behind them is rebuilt so the sim sees the race as it was at that point. Tray replays have **Jump to lap…** and **Pause** in the menu.
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

- **Without F1 TV:** timing, tyres and gaps are live; car positions on the map are estimated from timing. The app learns where this circuit's timing points really are, and how far round the lap a car is at each moment, from GPS in an earlier session (that weekend's latest, else the circuit's latest in the two seasons before; cached). Measured against real GPS on 2026 races, median distance from the real car: Australia 7 m, Britain 8 m, Spain 8 m, Monaco 8 m, Baku 8 m, Monza 11 m, Suzuka 13 m, Bahrain 18 m (was 90–320 m). Hungary stays ~120 m: its race GPS runs ~2.5 s behind its timing, compared with qualifying, so GPS and timing disagree there. Without a usable earlier session (new layout, other timing loops) it falls back to the old estimate (100–300 m).
- **With F1 TV:** tray → **Sign in to F1 TV…**, log in in the browser, then restart the live tracker for real GPS positions.

Tray → **Replay a past session** (year → weekend → session, same menu as F1 Live) plays any finished session from 2018 on through the same widgets (speed adjustable), so you can try everything between race weekends.

## Strategy sim knobs

Model settings live in `%APPDATA%\f1tracker\config.json` under `"model"` (tray → **Edit sim model knobs**), e.g.:

```json
"model": {"fuel_start_kg": 70, "pass_threshold": 1.0, "sc_per_race": 0.5}
```

Restart the app after editing. Defaults and their meanings are in `MODEL` at the top of `f1tracker/sim.py`.

### What the sim models

- **Lap times**: fitted per driver, compound offset + wear per lap + one trend for fuel burn and track evolution.
- **Tyre choice per car**: at each stop, the dry compound that's quickest to the flag (an extra stop if it won't last), and one the car hasn't used while it still owes the two-compound rule. Sprints have free tyre choice, so no forced stop.
- **Weather**: the track stays as it is now (what most cars are on) unless you pick **rain (inters) from lap** or **track dries from lap** under the pit row. Cars on the wrong tyres stop when the time they'd lose to the flag beats the pit loss. Slicks on a wet track lose `slick_wet_s` (6 s) a lap. No forecast is assumed.
- **Safety car / VSC / red flag**: random, at the rates seen in that era's races, and one that's out right now is always used: the sim starts under it with the laps it likely has left (an SC "in this lap" has one). Pick **safety car / VSC / red flag on lap N** under the weather row to see the best window and tyre if one comes then.
  - SC: nobody passes, a stop costs the SC pit loss, and by the end of each SC lap the field has closed up. A car that pits as the SC comes out rejoins in its gap and then catches the queue (the "free" stop); pitting once the queue has formed costs places.
  - VSC: gaps freeze, a stop costs the VSC pit loss.
  - Who takes a stop under an SC/VSC: odds fitted on that era's races (tyre age, laps left, still owing the two-compound rule, first or second lap of it, SC or VSC), plus a factor shared by every car in one SC/VSC: in real races teams mostly all pit or all stay out (the number of stops per SC varied 9–18× more than independent cars would give). SC and VSC each have their own odds and spread.
  - Red flag: the field closes up and every car gets new tyres for free, which counts for the two-compound rule.
  - Restarts: DRS / Overtake Mode stays off for the first racing laps after an SC or red flag (2 laps until 2023, 1 from 2024; 2026: once every car has passed the detection point), with passing odds fitted on restart laps.
- **Overtaking**: a car within 1 s at the line (Overtake Mode in 2026, DRS before) gets past with odds fitted from real passes, given its pace edge and the gap.
- **Lapped cars**: their gap is the car ahead's plus their interval, which the timing keeps in seconds.

### Calibration (per regulation era)

Each regulation era is a different car, so the sim is calibrated per era and a session uses its own era's numbers: **2018–2021** (wide cars, DRS), **2022–2025** (ground effect, DRS), **2026–** (new power units, active aero, Overtake Mode). Per-season regulation facts sit on top: minimum mass (733 kg 2018, 743 2019, 746 2020, 752 2021, 798 2022–24, 800 2025, 768 2026) and race start fuel (105 kg limit in 2018, about 100 kg used 2019–25, about 70 kg in 2026).

`f1tracker/calibration.json` holds, per era: SC/VSC rates and lengths, SC/VSC lap times, overtaking odds, dry tyre offsets and wear, and tyre life (90th percentile stint at the in-lap). Config overrides still win over it. To refit:

```
.venv\Scripts\python -m f1tracker.calibrate --write          # every era (about an hour the first time: downloads ~220 races)
.venv\Scripts\python -m f1tracker.calibrate 2026 --write     # just the 2026 era, after more races
```

It prints a backtest per race: lap-time error, finishing-position error, rank correlation, log score of the real result and "pit now" rejoin error, before and after calibration, each race left out of its own fit. Sim started at 30/50/70 % distance:

| Era | races | position error before → after | log score before → after |
|---|---|---|---|
| 2018–2021 | 83 | 1.65 → 1.60 | −2.73 → −2.22 |
| 2022–2025 | 113 | 1.66 → 1.57 | −2.68 → −2.14 |
| 2026– | 21 | 1.32 → 1.24 | −2.40 → −1.87 |

Who pits under an SC/VSC is a fitted chance, as in real races, so the spread of outcomes is more realistic while the average position is a few hundredths of a place less sharp than a fixed "worn tyres pit" rule. Checked on seasons the fit never saw (`scripts/validate_pit_neutral.py`, results in docs/CHECKLIST.md): the fitted odds predict pit/no-pit 2–2.5× better (Brier) and give a better log score in every era; the shared per-SC factor reproduces how clustered real SC stops are.

The eras really differ, for example SCs per race 0.78 / 0.70 / 0.62, VSCs 0.52 / 0.48 / 0.95, and different overtaking odds and tyre wear. Scored on the 2025 races, their own era's fit beats the 2026 fit only slightly (1.64 vs 1.67 places, same log score): from 30 % distance the running order dominates. "Pit now" rejoin: 0.67 places off on average in 2026 (1.38 with the old 95 s lapped-car gap).

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
