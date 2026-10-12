# Implementation checklist

Status legend: `[x]` done and validated · `[~]` partly done (see note) · `[ ]` not started.
Plan and design notes: [PLAN.md](PLAN.md). Manual test steps per group: [TESTING.md](TESTING.md).

**Where it stands (2026-10-11):** everything below is on `main` (PR #5, #6). Left: item 21 (PyInstaller) and the live test of going live by itself, alerts and Sync delay at the US GP (FP1 23 Oct; F1 TV sign-in runs out 15 Oct, sign in again first). 85 unit tests, 3 integration tests and both smoke scripts pass.

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
- [x] `lightsout/dashboard.py`: one window, big map left, timing tower top right, strategy sim bottom right, no standings
- [x] Connects live on launch; toolbar: Go live / Replay (+ speed) / Stop / Sign in to F1 TV; status line with session, lap, track status, weather
- [x] F1 TV sign-in from the window, then reconnects with auth, keeping the State (laps logged so far)
- [x] Widgets reusable as docked panels (`Widget(app, key, parent)`); map markers scale with size; label stacks capped at 4 (+N)
- [x] Docked timing tower: interval, gap to leader, last, best, tyre, stops
- [x] Launchers: `F1 Live.bat`, `run_live.pyw`, tray → Open live dashboard
- [x] **Live feed bug fixed:** without F1 TV the connection failed (`access_token_factory is not function`, FastF1 3.8.3 + signalrcore 1.0.2). Verified live connection afterwards
- [x] Live feed can now actually stop (FastF1's loop only ended after 120 s without data)
- [x] `scripts/smoke_dashboard.py`

## Added on request (2026-10-06): desktop icons, split opacity
- [x] `lightsout/f1.ico` (256 px, same red F1 badge as the tray) + `scripts/create_shortcuts.ps1` → desktop shortcuts **F1 Live** and **F1 Tracker** (created on this PC)
- [x] Opacity split into **Text opacity** (labels, controls, map drawing; via QGraphicsOpacityEffect) and **Background opacity** (painted alpha, min 1/255 so it stays draggable)
- [x] Old `opacity` setting migrated: text = opacity, background = opacity × 225/255 (same look as before); `tests/test_config.py`

## Fixed 2026-10-06: F1 TV sign-in from shortcuts
- [x] Root cause of `Could not connect to the local FastF1 application`: started from a shortcut (pythonw.exe), `sys.stderr` is None. FastF1's login server logs every request to stderr, so it crashed before replying to the extension. Reproduced, then fixed (quiet handler)
- [x] stdout/stderr go to `%APPDATA%\lightsout\tray.log` / `live.log` under pythonw, so nothing else can crash the same way
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
Done 2026-10-06 on branch `sim-calibration` (pushed 2026-10-07, PR to `main` pending). Scored by `python -m lightsout.calibrate` (leave-one-race-out over the 21 races and sprints of 2026, see README).
- [x] 5. Per-car tyre choice: cheapest dry compound to the flag (extra-stop penalty past its life), two-compound rule per car. **Sprints have free tyre choice** (verified: the rule only applies to Grands Prix); the sim used to force a stop in sprints. No set inventory (new vs used sets)
- [x] 6. Wet / inters: `conditions` (DRY / WET, or from lap N), default holds what most cars are on. No forecast invented; the sim panel has "rain (inters) / track dries from lap N". Wrong tyres stop when the time lost to the flag beats the pit loss. `slick_wet_s` = 6.0 s, median of 69 laps in the 5 mixed races of 2025-26 (wide spread). Inters on a dry track: fitted offset (too few laps to measure). Full wets folded into WET (rare in races)
- [x] 7. Lapped cars: gap = car ahead's gap + interval (seconds even when the gap reads "1L"); leader's median green lap only when the interval is in laps. Rejoin error 1.38 → 0.84 places over 303 stops. Fixed a crash: "6L" gaps raised ValueError in the old parser
- [x] 8. Overtaking: within 1 s at the line (2026 Overtake Mode: +0.5 MJ when within 1 s at one detection point per lap; DRS before) the car behind gets past with P = sigmoid(a + b·pace edge + c·gap), fitted on real passes. Pit / SC / VSC laps excluded. Lapped cars never block (they sit a lap behind in race time). Dirty air not separate: it's in the fitted odds
- [x] 9. Calibration **per regulation era** (2018–21, 2022–25, 2026–; user's call: each era is a different car): `lightsout/calibrate.py`, `calibration.json` → `sim.season_model(year)` (defaults < season facts: min mass + start fuel per year, verified < era calibration < config). Also fits dry tyre offsets/wear and tyre life per era; 2018's seven compound names relabelled to the race's SOFT/MEDIUM/HARD. Leave-one-out per era: 2018–21 1.65→1.56 places, 2022–25 1.66→1.55, 2026 1.32→1.22. Own-era vs 2026 fit on 2025 races: 1.64 vs 1.67 (small). SC/VSC per race + length from TrackStatus, SC/VSC lap factors, pass odds. Lap-fit knobs checked on a 4×5 grid: current prior weight 3 / half-life 4 laps were already best (0.678 s). VSC added to the sim

## Added on request (2026-10-06): SC / VSC / red flag in the sim
- [x] An SC / VSC / red flag that's out now: `State.neutral` (TrackStatus 4/6/7/5, race control's SafetyCar "IN THIS LAP") → `State.neutral_now()` → sim starts under it with the laps it likely has left
- [x] "What if a safety car / VSC / red flag on lap N" row in the sim panel (What if and Best window), scenario shown above the result. Result area scrolls
- [x] SC: stops first, then the field closes up by the end of each SC lap (the stop as the SC comes out is the cheap one; later ones cost places)
- [x] Who pits under SC/VSC: logistic odds per era (`pit_neutral`: tyre age share, laps left, owes the two-compound rule, 1st/2nd lap of it, VSC) + a random intercept shared by all cars in one SC/VSC (`pit_neutral_sd`, own spread for SC and VSC; Gauss-Hermite marginal likelihood). Gap-to-car-behind feature tried: no better, dropped
- [x] Out-of-sample validation (user's request, 2026-10-07), `scripts/validate_pit_neutral.py`: fit on earlier seasons of an era, test on its latest (2021, 2025, last 7 races of 2026). Worn rule / per-car odds / + shared factor:
  - pos error 1.45/1.50/1.48 (2021), 1.64/1.67/1.66 (2025), 1.38/1.42/1.41 (2026): worn rule best by 0.02–0.05
  - log score −2.14/−2.08/−2.06, −2.25/−2.14/−2.16, −2.09/−1.98/−2.03: fitted better everywhere; RPS equal; 80 % interval coverage 0.69/0.75/0.73, 0.69/0.73/0.72, 0.69/0.71/0.68 (all a bit narrow)
  - pit/no-pit Brier 0.27/0.11/0.10, 0.16/0.07/0.07, 0.28/0.13/0.13; stops-per-SC spread ratio (1 = right) –/18/1.2, –/8.9/0.8, –/21/1.5
  - weak spots: the 2026 holdout under-predicts stops (SC 14 % vs 24 %, VSC 5 % vs 18 %; 14 races to fit on); 2025's rare >75 % calls were over-confident (86 % vs 20 %, n=10). Refit after more 2026 races
- [x] Red flags per era (`red_per_race` 0.12–0.14): free tyre change for all (counts for the two-compound rule, verified), field closes up
- [x] Restarts: no DRS / Overtake Mode for `restart_laps` (2 until 2023, 1 from 2024; 2026 OM after every car passes the detection point; verified), passing odds `pass_restart` fitted on restart laps
- [x] Backtests start under the SC/VSC as it really was. Effect on all eras: log score +0.13–0.15, mean position error +0.03–0.05 places (more realistic spread, slightly blurrier mean)
- [x] Tests: SC now bunches + restart passing, SC-on-lap-N best window, red-flag free tyres, `State.neutral_now`

## Group 3 — Live / data
- [x] 10. Automatic live-session detection (2026-10-11): by the Jolpica schedule (10 min before → 30 min after), kept on past it while `SessionStatus` is Inactive / Started / Aborted and messages arrive (`State.still_on`), F1's `StreamingStatus.json` polled every 5 min outside it (Offline → Available starts live; after an automatic stop only a fresh Offline → Available does). Tray / Settings switch, default on; switching it off mid-session keeps the current one. To test live at the US GP
- [x] 11. Better estimated positions (2026-10-06). The circuit API has no `trackPositionTime`, so `live.learn_positions` learns from archived GPS: where each timing mini-sector ends (median GPS spot as cars complete it, per sector, so a sector with another loop count spreads evenly between learned ends) and a time→distance profile of a green lap. Source: that weekend's latest finished session, else the circuit's latest in the two seasons before; measured on the current outline; cached per source + outline. Used by live feeds and no-GPS replays, on a background thread. Median error vs GPS on 9 races of 2026: 7–18 m on 8 (was 90–320 m); Hungary 153→120 m (its race GPS/timing offset differs from qualifying). Suzuka's crossover handled (search near the car's last spot); two loops at one spot allowed. Bad fits (loops clearly out of order, e.g. another layout) are rejected → old estimate
- [x] 12. Replay: older seasons (2026-10-06). Year → weekend → session menus in F1 Live and the tray, 2018 to now. Sessions come from the archive's Index.json plus FastF1's schedule for the sessions the index leaves out (2018 Australia, 2024 before Spain, 2023 Qatar after FP1), kept only if the archive has their TimingData. F1 refuses most of 2022 (403); archive requests fall back to FastF1's mirror (`livetiming-mirror.fastf1.dev`, as FastF1 does), which has it. 2018 tyres use Pirelli's 2018 names and colours (`TYRE_2018`). Each year loads once per run when its menu first opens
- [x] 13. Qualifying: Q1/Q2/Q3, countdown, knockout zone, sprint quali: live dashboard (sector timing) and, 2026-10-06, the tray TimingTower too (best of the current part, drop zone red, knocked out greyed). Checked on the Bahrain 2026 qualifying replay in Q2
- [x] 14. Projected points in replays (2026-10-06): `ChampionshipPrediction` added to replays (in the archive from mid-2019). Earlier: Jolpica standings before the session (`data.standings_before`: after the previous round, plus the weekend's sprint for its Grand Prix) + that season's points (fastest-lap point 2019–2024 in the top 10, sprint 3-2-1 in 2021, 8–1 from 2022; verified). 2021's sprint ('Sprint Qualifying', Type Race) now counts as a sprint. Checked: Abu Dhabi 2018 → HAM 408, VET 320, Mercedes 655 (real final table); Silverstone 2021 sprint VER +3 HAM +2 BOT +1

## Added on request (2026-10-09): dashboard look, monitors, live countdown
- [x] Live dashboard restyled: dark panels with rounded corners and gaps, Bahnschrift condensed type (ships with Windows), upper-case tabs with a red underline, styled menus/combos/sliders (`chevron.svg` for the combo arrow), zebra rows in docked tables
- [x] Status line as chips: feed (green dot while live), session, lap, track status in its flag colour
- [x] Timing tower: session's fastest lap in purple. Sim: the four likeliest finishes as a shaded bar. Map: verge + tarmac outline
- [x] Widgets off every screen (monitor unplugged or rearranged) move onto the nearest screen's work area, at start and on screen changes (`widgets.onto_screen`)
- [x] Next race shows `<session> LIVE` while a session runs (`data.live_session`, scheduled lengths; a red flag that runs over ends it early)
- [x] Tests: `onto_screen` (2 monitors, offset), `live_session`. 53 pass
- [x] README: animated banner (`docs/banner.svg`, real Marina Bay outline from MultiViewer, SMIL so it plays on GitHub), badges, section links

## Added on request (2026-10-11, after the Singapore race): one app, battles, alerts
- [x] Map rubber-banding fixed: GPS samples timed by their own clock plus the quickest delivery seen (live messages arrived up to 1.2 s late). On 2 min of the Singapore race: 841 → 0 frames with a car going backwards
- [x] Live sessions recorded (`sessions/<key>.jsonl`) and replayed after a restart: the lap log survives
- [x] Tray and dashboard are one app (`main.App` owns one live feed; the dashboard is its window). One desktop shortcut, `F1 Live.bat` / `run_live.pyw` gone; Start with Windows adds `--tray`. Settings (delay, names size, replay speed) moved into `config.json`
- [x] Goes live by itself from 10 min before each session to 30 min after (item 10's goal, by the schedule rather than by probing the feed). A replay can play while live keeps recording. Tray icon green dot + status tooltip
- [x] Radio transcription model freed when the dashboard closes
- [x] Pit loss: the race's own stops from the third, before MultiViewer's value (`scripts/validate_pit_blend.py`: 799 stops of 2025–26, loss 1.18 → 0.84 s off, rejoin 1.35 → 1.30 places). Singapore published 27.6 s, measured 24.2 s
- [x] Telemetry card removed from the map
- [x] My drivers (follow + alerts) and my team in Settings; all names on the map, followed drivers ringed; highlighted tower rows; sim opens on the first
- [x] Battles tab / widget: on-track gap, gap net of stops still owed, pace edge, catch laps, tyres, gap trend, real rival with pin; pinned pairs (+ Battle, tower right-click)
- [x] Alerts (races/sprints, live, after the delay): pit window, rival pitted, SC/VSC cheap stop, stewards/retirement, 2+ places; team double stack, teammates fighting/swapping, slow stop. Dashboard popup while open, else Windows notification
- [x] ⏱ Sync delay (or S): delay from the lap counter change on TV
- [x] Sim refit after each race in the background, offered with the backtest change (tray → Apply new sim fit), history in tray → Strategy sim accuracy. 2026 refitted with Singapore: 23 races
- [x] Sim fit keeps traffic laps for a driver left with fewer than 3 free laps (found by the pit window alert on Bahrain 2026: HAM P9 → simulated P21). 2026 out of sample: position error 1.21 → 1.13, log score −1.77 → −1.70, lap error 0.689 → 0.675 s
- [x] `PitStopSeries` not sent live even with F1 TV (checked during the Singapore race)
- [x] Tests: alerts, battle math, stops owed, delay sync, pit loss order, traffic laps, log, still-on, sign-in, cache, settings override, live.txt cap. 85 pass
- [x] Renamed **Box Box** (was F1 Tracker): repo `soham2121/box-box` (was `f1-thing`), package `boxbox`, data folder `%APPDATA%\boxbox` (moved from `f1tracker` on the first start, when the old app isn't running), Start with Windows entry and desktop shortcut renamed the same way
- [x] Renamed **Lights Out** (2026-10-12; "Box Box" was taken by another app): package `lightsout`, data folder `%APPDATA%\lightsout` (moved from `boxbox` on the first start), Start with Windows entry and desktop shortcut renamed the same way. Setup for new users (`Setup.bat`: installs Python if missing, asks live dashboard only or with widgets); `desktop_widgets` setting and tray toggle. 87 tests pass

## Group 4 — UX
- [x] 15. Settings dialog: one dialog for the app (tray or dashboard), applied at once. Strategy sim section (2026-10-11): SC / VSC / red flags per race, soft / medium / hard tyre life, fallback pit loss, each showing the era's fitted value, lowest = fitted, reset button; a table entry (one compound's life) merges into the fitted table (`sim.season_model`). Other knobs stay in config.json (tray → More sim knobs). 84 tests pass
- [x] 16. Desktop layer survives Win+D (done early on request, 2026-10-06). Rainmeter's method: every 250 ms, if the foreground window is the desktop (shell window, or WorkerW/Progman hosting SHELLDLL_DefView) the widgets go topmost, otherwise back to the bottom. Qt's WindowStaysOnBottomHint had to go: Qt forced those windows back to the bottom. Verified with Show desktop on this PC (Windows 11 build 26200)
- [x] Single tray instance (on request): named mutex `Local\lightsout-tray-<user>`; a second launch notifies the first over QLocalSocket and exits
- [x] 17. Logging with rotation (2026-10-11): `%APPDATA%\lightsout\logs\lightsout.log`, 1 MB × 5. stdout/stderr become timed log lines (prints, Qt tracebacks, FastF1's console output, once each), still echoed to a console when there is one; unhandled exceptions in any thread and background-job failures logged with tracebacks; JWTs (F1 TV tokens) replaced by `<token>`. Replaces the per-launch `tray.log` / `live.log` (deleted on the first start). FastF1's raw `live.txt` (47 MB after the Singapore weekend, appended forever, read by nothing) starts afresh past 50 MB, one old copy kept Tests: token filter, line capture; checked on a live connection
- [x] 15b. FastF1 auth in `lightsout/auth.py` (2026-10-11): `check()` → (token, message, expiry), never raises; FastF1 changing its private names gives `F1 TV sign-in unavailable: FastF1's sign-in code changed (no …)`. The live connection uses the checked token itself instead of FastF1's `get_auth_token()`, which opens a browser and blocks when the token has expired (the saved token runs out on 15 Oct, before the US GP: the old code would have hung there). Sign-in state shown in the tray menu and on the dashboard; a tray warning when it runs out before the next session. Checked live: with the token GPS + projection arrive, without they don't

## Group 5 — Distribution
- [x] 18. `requirements.txt` with direct deps (2026-10-11): `fastf1==3.8.3` pinned (private sign-in / live client code), `signalrcore` 1.0.x (its no-auth bug is patched in live.py), PySide6, numpy, scipy, requests (were only installed through FastF1), faster-whisper; lower bounds = the tested versions. `pip install --dry-run` resolves with nothing to change
- [x] 19. Test suite expansion (cache, sim, replay). Sim (`tests/test_sim.py`) and replay tests added with Groups 2–3; cache (`tests/test_cache.py`, 2026-10-11, `requests` faked): fresh hit, stale refresh, offline → stale, offline without cache raises, history forever, archive → mirror fallback, BOM. 83 pass
- [x] 20. Start with Windows: tray toggle, HKCU Run key, off by default (done early on request, 2026-10-06). `run.pyw` + `F1 Tracker.bat` launchers. Widget positions saved 1 s after each move/resize, not only on Quit
- [ ] 21. PyInstaller build script
