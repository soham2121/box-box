# Implementation checklist

Status legend: `[x]` done and validated · `[~]` partly done (see note) · `[ ]` not started.
Plan and design notes: [PLAN.md](PLAN.md). Manual test steps per group: [TESTING.md](TESTING.md).

## Group 1 — Correctness ✅ (ready for user testing)

### 1. Fallback pit-loss bug
- [x] Real stops read from the `PitStopSeries` topic (`State.stops()`), lap pit-flags only as a fallback
- [x] Pit flags ignored before the session starts (lap 1 is no longer a "pit lap" for every car)
- [x] Neutralised laps: TrackStatus SC/VSC/red + statistical check (field median > 1.15× session median), catches SC starts that TrackStatus misses
- [x] Per-stop loss vs the driver's own laps either side (field-corrected); field reference as fallback
- [x] Slow stationary times (wheel problems, penalties) replaced by the session's typical stationary time; MAD outlier rejection
- [x] Source order: circuit API → circuit catalogue (same values, covers more years) → this session (≥ 3 stops) → sprint of the same weekend → catalogue median → model default; source shown in the sim panel
- [x] SC / VSC pit loss from MultiViewer, else MultiViewer's ratios (0.634 / 0.723)
- [x] "Pit now" rejoin uses the SC / VSC pit loss while the SC / VSC is out
- [x] Unit tests (`tests/test_pitloss.py`) + integration test on Bahrain 2026
- [x] Validated against MultiViewer on 12 races of 2026: median error +0.56 s, MAE 1.5 s

### 1b. Found during validation: practice tyre priors
- [x] `priors_from` paired quali-sim laps with race-fuel runs and read the fuel gap as tyre wear (0.6 s/lap on softs). Now only real consecutive-lap runs, with an intercept per run and a fuel correction; compound offsets keep the model prior

### 2. Sprint projected points
- [x] Primary: F1's own `ChampionshipPrediction` feed (drivers and constructors), so points never count twice
- [x] Fallback: Jolpica per-round results/sprint presence (`data.round_counted`) instead of the standings `round`
- [x] Sprint Qualifying no longer gets sprint points (it was matched by name)
- [x] Constructor widget shows the projection too
- [x] Jolpica refetched every 5 min during a live feed so the fallback never stacks on fresh results
- [x] Unit tests (`tests/test_points.py`) + integration test on Jolpica

### 3–4. Regression tests and revalidation
- [x] `pytest` set up (`pytest.ini`, `requirements-dev.txt`, `tests/`), integration tests opt-in
- [x] Azerbaijan backtest unchanged (lap MAE 0.356 s)
- [x] Bahrain replay smoke script (`scripts/smoke_replay.py`)
- [x] App launches

## Added on request (2026-10-06): live dashboard
- [x] `f1tracker/dashboard.py`: one window, big map left, timing tower top right, strategy sim bottom right, no standings
- [x] Connects live on launch; toolbar: Go live / Replay (+ speed) / Stop / Sign in to F1 TV; status line with session, lap, track status, weather
- [x] F1 TV sign-in from the window, then reconnects with auth, keeping the State (laps logged so far)
- [x] Widgets reusable as docked panels (`Widget(app, key, parent)`); map markers scale with size; label stacks capped at 4 (+N)
- [x] Docked timing tower: interval, gap to leader, last, best, tyre, stops
- [x] Launchers: `F1 Live.bat`, `run_live.pyw`, tray → Open live dashboard
- [x] **Live feed bug fixed:** without F1 TV the connection failed (`access_token_factory is not function`, FastF1 3.8.3 + signalrcore 1.0.2). Verified live connection afterwards
- [x] Live feed can now actually stop (FastF1's loop only ended after 120 s without data)
- [x] `scripts/smoke_dashboard.py`

## Added on request (2026-10-06): desktop icons, split opacity
- [x] `f1tracker/f1.ico` (256 px, same red F1 badge as the tray) + `scripts/create_shortcuts.ps1` → desktop shortcuts **F1 Live** and **F1 Tracker** (created on this PC)
- [x] Opacity split into **Text opacity** (labels, controls, map drawing; via QGraphicsOpacityEffect) and **Background opacity** (painted alpha, min 1/255 so it stays draggable)
- [x] Old `opacity` setting migrated: text = opacity, background = opacity × 225/255 (same look as before); `tests/test_config.py`

## Fixed 2026-10-06: F1 TV sign-in from shortcuts
- [x] Root cause of `Could not connect to the local FastF1 application`: started from a shortcut (pythonw.exe), `sys.stderr` is None. FastF1's login server logs every request to stderr, so it crashed before replying to the extension. Reproduced, then fixed (quiet handler)
- [x] stdout/stderr go to `%APPDATA%1tracker	ray.log` / `live.log` under pythonw, so nothing else can crash the same way
- [x] Login server waits 30 min (was 5), also listens on `::1` (the extension calls `localhost`), and a second click reuses the waiting server; clear failure reasons (timeout / no F1 TV subscription / token didn't verify)
- [x] Single dashboard instance: a second launch brings the open window to the front

## Added on request (2026-10-06): replay seeking
- [x] `ReplayFeed.seek(t)` / `seek_lap(n)` / pause (speed 0). Backward jumps reset the State (`RESET` pseudo-topic) and fast-forward from the start; GPS messages only for the last 10 s of a jump. Bahrain lap 40 in 0.5 s
- [x] Dashboard replay bar: Pause/Play, ◀ Lap / Lap ▶ (rapid clicks add up), timeline slider, lap + clock, speed. Tray: Jump to lap…, Pause
- [x] **Bug fixed:** `merge()` stored incoming messages by reference and later deltas edited them in place, so replayed messages carried future data. It now copies new parts
- [x] **Bug caught by the tests before shipping:** naming the attribute `start` would have hidden `Thread.start()`
- [x] `tests/test_replay.py` (start, jump forward, jump back, pause, finish, merge aliasing)

## Added on request (2026-10-06): dashboard settings, SC marker, wind, follow drivers, delay
- [x] **Settings…** dialog in the dashboard: driver name size, live data delay, names-for-drivers list. Saved in `dashboard.json`
- [x] **Safety car marker.** 241/242/243 are all the safety car (checked against 2025–26 race archives: each moves only while TrackStatus = SC). The old code called 242 the medical car, which was wrong. Now a yellow `SC` dot, shown only while it moved in the last 30 s (it's parked in the pit lane otherwise)
- [x] Track status text spaced: `SCDeployed` → `SC Deployed`, `VSCEnding` → `VSC Ending`, `AllClear` → `All Clear` (`State.session()`, so everywhere)
- [x] **Wind compass** on the map. Checked first:
  - Position X/Y are east/north. Outlines fit OpenStreetMap within 2° at Monza, Silverstone, Spa, Suzuka and Sakhir, not mirrored
  - `WindDirection` = where the wind comes from. 14 races of 2025 vs Open-Meteo history: median difference 37°, not ~180°
- [x] **Follow drivers**: all dots stay, names only for ticked drivers (SC always labelled)
- [x] **Delay**: `State.delay` holds live messages in `State.held` until they're N s old. Live feed only
- [x] **Stopped/retired cars removed from the map** in GPS mode too (they sat at their last GPS spot). Race archives: F1 sets `Stopped` when a car stops, clears it if the car restarts; `Retired` is rarely sent
- [x] `tests/test_live.py` (delay, status text, SC only while moving, stopped car)

## Added on request (2026-10-06): session layouts (inspired by undercut-f1's screens)
- [x] Dashboard right side follows the session type: race/sprint = tabs [Timing | Tyre stints] over [Strategy sim | Race control]; practice/qualifying = sector timing over [Tyres | Race control]. Map + wind always on the left
- [x] Sector timing (`SectorTower`), with fields checked on the Bahrain 2026 archive:
  - Mini-sector status bits: 2048 passed, +1 personal best, +2 overall best, +16 pit lane
  - F1 resets the segments and clears the sector times at each line crossing
  - Qualifying: `SessionPart`; `NoEntries` [22, 16, 10] gives the drop zone. Per-line `Cutoff` is the 107 % rule, not the drop zone. `BestLapTimes` / `Stats` are per part, and `KnockedOut` is set when the part ends
  - Countdown from `ExtrapolatedClock`. The Remaining value counts down from the message's arrival while Extrapolating (its Utc matched the feed time)
- [x] Tyre stints (`StintPanel`): laps per set = `TotalLaps − StartLaps` (TotalLaps is tyre age; checked: the sets add up to the 55 race laps). Last stop = PitStopSeries stationary time
- [x] Tyres (`TyrePanel`), Race control (`RaceControl`, newest first, flag colours, weather)
- [x] Replays also play `ExtrapolatedClock`
- [x] **Bug fixed:** the stopped-car change broke the estimated map (no F1 TV): a list reused the name of the helper. Found by the new screenshots; test added
- [x] Tests: clock, quali part / drop zone, estimated map with a stopped car

## Group 2 — Simulator
- [ ] 5. Per-car tyre choice (known effect now: what-if overstates gains from switching compound, see TESTING.md)
- [ ] 6. Wet / intermediate tyres and condition changes
- [ ] 7. Lapped-car rejoin from session pace (no fixed 95 s)
- [ ] 8. Overtaking model (pace, gap, dirty air, Overtake Mode, lapped traffic)
- [ ] 9. Calibration across many races, prior weights, multi-race metrics

## Group 3 — Live / data
- [ ] 10. Automatic live-session detection (tray toggle, default on)
- [ ] 11. Better estimated positions (`trackPositionTime`, calibrated mini-sectors)
- [x] 12. Replay: older seasons (2026-10-06). Year → weekend → session menus in F1 Live and the tray, 2018 to now. Sessions come from the archive's Index.json plus FastF1's schedule for the sessions the index leaves out (2018 Australia, 2024 before Spain, 2023 Qatar after FP1), kept only if the archive has their TimingData. F1 refuses most of 2022 (403); archive requests fall back to FastF1's mirror (`livetiming-mirror.fastf1.dev`, as FastF1 does), which has it. 2018 tyres use Pirelli's 2018 names and colours (`TYRE_2018`). Each year loads once per run when its menu first opens
- [~] 13. Qualifying: Q1/Q2/Q3, countdown, knockout zone, sprint quali: **done in the live dashboard** (sector timing, 2026-10-06). The tray TimingTower widget still shows plain best laps
- [ ] 14. Projected points in replays (incl. fastest-lap point 2019–2024)

## Group 4 — UX
- [ ] 15. Settings dialog (validated, saved, applied without restart)
- [x] 16. Desktop layer survives Win+D (done early on request, 2026-10-06). Rainmeter's method: every 250 ms, if the foreground window is the desktop (shell window, or WorkerW/Progman hosting SHELLDLL_DefView) the widgets go topmost, otherwise back to the bottom. Qt's WindowStaysOnBottomHint had to go: Qt forced those windows back to the bottom. Verified with Show desktop on this PC (Windows 11 build 26200)
- [x] Single tray instance (on request): named mutex `Local1tracker-tray-<user>`; a second launch notifies the first over QLocalSocket and exits
- [ ] 17. Logging with rotation
- [ ] 15b. FastF1 auth isolated in one module, graceful failures

## Group 5 — Distribution
- [ ] 18. `requirements.txt` with direct deps, FastF1 pinned
- [ ] 19. Test suite expansion (cache, sim, replay)
- [x] 20. Start with Windows: tray toggle, HKCU Run key, off by default (done early on request, 2026-10-06). `run.pyw` + `F1 Tracker.bat` launchers. Widget positions saved 1 s after each move/resize, not only on Quit
- [ ] 21. PyInstaller build script
