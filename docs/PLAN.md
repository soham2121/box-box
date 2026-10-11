# Box Box (was F1 Tracker) — correctness fixes + feature plan

**Status (2026-10-11):** Groups 1–2 done; Group 3 done (item 10: schedule + F1's streaming flag + late-running sessions, switch in Settings; to test live at the US GP). Group 4 done. Group 5: 18, 19 and 20 done, 21 (PyInstaller) to do. Also done on request: SC / VSC / red flag in the sim, per-era calibration, dashboard restyle, one app with battles and alerts, renamed **Box Box**. Progress: [CHECKLIST.md](CHECKLIST.md).

## Decisions log (changes to the plan below, with evidence)
- **With F1 TV (checked 2026-10-06):** Position.z, CarData.z and ChampionshipPrediction arrive. PitStopSeries was not in the snapshot; check in a live session via live.txt, the fallback covers it.
- **F1 TV sign-in flow (from the FastF1 Companion extension's own code).** The f1login page saves `?port=`, redirects to account.formula1.com, then the extension POSTs the `login-session` cookie to `http://localhost:<port>/auth`. The app only hosts that endpoint, so private FastF1 code is still needed (AuthHandler, _auth_finished, _subscription_token).
- **Win+D (item 16), Rainmeter's z-order approach, not SetParent into WorkerW.** No reparenting, so dragging and saved positions behave as before. `Qt.WindowStaysOnBottomHint` was replaced by our own z-order handling, because Qt kept forcing those windows back to the bottom. Verified with Shell.Application.ToggleDesktop on build 26200.
- **2026-10-06, live dashboard added on request** (new window, not in the original 20 items).
  - The live feed without F1 TV was broken by FastF1 3.8.3 + signalrcore 1.0.2. Fixed in our client subclass.
  - Verified without auth, the feed has: TimingData, TimingAppData, TyreStintSeries, TrackStatus, WeatherData, RCM, LapCount.
  - Missing without auth: PitStopSeries, ChampionshipPrediction, Position.z, CarData.z.
- **Pit loss, no `loss <= PitLaneTime` bound.** Baku's measured loss (21.5 s) is above its pit-lane time (20.7 s): the timing points don't span the whole loss.
- **Pit loss, own-lap reference first.** Validated against MultiViewer on 12 races of 2026: own laps either side gave an MAE of 1.5 s, the field-only reference 2.0 s. The field reference stays as a per-stop fallback.
- **Pit loss, minimum 3 stops.** With 2 stops the estimate was up to 15 s off (British sprint 36.5 s against 20.9 s).
- **Pit loss, no "same circuit, previous year" archive step.** The MultiViewer catalogue already covers every circuit with history, and new venues (Bahrain 2026 at key 12) have no history anywhere. The catalogue median is the honest last resort.
- **Pit loss from pit-lane time was rejected.** Across 40 races it did not track MultiViewer's value (Silverstone lane 29 s against a 20.9 s loss, Zandvoort 18 s against 22.7 s).
- **Neutral laps.** RCM free-text ("SAFETY CAR LIGHTS ON", "STANDING START") is fragile. The statistical slow-lap check (1.15x) catches the same laps, so no RCM parsing.
- **Bahrain 2026 real pit loss is ~27 s, not ~22 s.** Its pit lane takes 27.6–29 s to drive through. The "about 22 s" in the original bug report was an assumption.
- **Added: practice-prior fix (`priors_from`).** Found while validating the what-if: quali-sim laps paired with race runs gave 0.6 s/lap deg.
- **2026-10-06, replays back to 2018.** The archive starts in 2018. F1 refuses most of 2022 (403), so archive requests fall back to FastF1's mirror.
- **2026-10-06, sim calibrated per regulation era (2018–21, 2022–25, 2026–), user's call.** Each era is a different car, so one fit for all is wrong. Season facts (min mass, start fuel, restart DRS laps) are set per year on top.
- **2026-10-06, SC out now from race control's structured fields.** The sim needs to know an SC is coming in, so `State.neutral` reads `Category: SafetyCar` + `Status: IN THIS LAP`, not free text. The slow-lap check above still finds neutral laps for pit loss.
- **2026-10-11, after the Singapore race:**
  - `PitStopSeries` is not sent live even with F1 TV (checked during the race). Live pit stops come from the lap pit flags.
  - **Pit loss: the race's own stops first** (from the third), before MultiViewer's value. Reverses the order above: on 799 green-flag stops of 2025–26 (`scripts/validate_pit_blend.py`) the loss was 0.84 s off instead of 1.18 s, the rejoin 1.30 places off instead of 1.35; blends did no better. Singapore: published 27.6 s, measured 24.2 s.
  - **Tray and dashboard became one app** (user's call): one live connection, one recording, one settings file; alerts need to know whether the dashboard is open.
  - **Item 10 by the schedule**, not by polling `StreamingStatus.json`: live from 10 min before each session to 30 min after. Simpler and enough for the schedule we have; a session that overruns by more than 30 min is the known gap.
  - **Sim fit keeps traffic laps** for a driver who'd have fewer than 3 laps without them (Bahrain 2026: HAM P9 simulated at P21). All eras better out of sample (2026 position error 1.21 → 1.13).
  - **Renamed Box Box** (user's choice), repo `soham2121/box-box`.
- **2026-10-07, fitted "who pits under SC/VSC" model kept (user's call).** Out of sample it loses 0.02–0.05 places of mean error to a worn-tyre rule but wins on log score, pit/no-pit calibration and the spread of stops per SC. The user prefers realistic distributions to a slightly better mean.

## Context
The user's spec lists 20 items in 5 priority groups. Most urgent: the strategy sim gives nonsense at Bahrain 2026. Pit loss shows 105 s instead of about 22 s, so the panel says "rejoin P19" and "loses 12.98 places". Work runs group by group. After each group: update the docs, give a summary and a full manual test plan, then **stop and wait for the user**. **No git commits** (the user commits).

Codebase: `boxbox/` (data.py, live.py, sim.py, widgets.py, main.py; ~1,400 lines). `.venv` is installed (Python 3.11, fastf1 3.8.3, PySide6 6.11.2).

## Facts verified during planning (read-only probes + web)
- **Bahrain root cause.**
  - A red flag stopped the session before the start. The race then began behind the SC (RCM "SAFETY CAR LIGHTS ON", then "STANDING START" on lap 2).
  - TrackStatus never went to `4` during those laps, so laps 2–3 (155–190 s) are not flagged `sc`.
  - Every lap 1 is flagged `pit`.
  - `pit_loss_from` (sim.py:103) pairs these laps as "stops".
- **`PitStopSeries` archive topic** holds real stops: `{RacingNumber, Lap, PitStopTime, PitLaneTime}`. The replay already subscribes to it; the live feed adds it at live.py:318. Bahrain shows real stops plus pre-race/SC-lap entries with lane times of 38–81 s.
- **MultiViewer `pitLoss`** returns `{normal, sc, vsc}` (Baku: 21.80 / 13.81 / 15.77). The code ignores `vsc`.
  - MultiViewer **no longer returns `miniSectorsIndexes`**, so the map always uses the equal-length fallback.
  - It does return `trackPositionTime`: a timestamp per outline point (speed profile of a reference lap).
- **`ChampionshipPrediction` topic** exists live and in the archive, for both race and sprint sessions. Per driver (by racing number) and per team it gives `CurrentPoints` and `PredictedPoints`. This is F1's own projection, and it solves the sprint double-count.
- **Jolpica**: standings `round` already equals the sprint round after a sprint (the bug). `/current/{round}/results.json` and `/current/{round}/sprint.json` show per session whether results are counted. `current/last/sprint.json` is useless (it gives the last round, with empty races).
- **Points 2026**: race 25-18-15-12-10-8-6-4-2-1; sprint 8..1 for the top 8. **No fastest-lap point since 2025** (it was given 2019–2024, top 10 only).
- **Tyres**: two dry specs are mandatory *unless intermediate or wet tyres were used* in the race.
- **Quali 2026**: 22 cars; Q1 18 min, Q2 15 min, Q3 12 min; 6 knocked out in Q1 and in Q2. Sprint quali uses the same structure.
  - Live timing provides `SessionPart`, `NoEntries {22,16,10}`, per-line `KnockedOut`, `Cutoff`, `BestLapTimes[part]`, and an `ExtrapolatedClock` topic (`Remaining`, `Utc`, `Extrapolating`).
- **Overtaking 2026**: DRS has been replaced by "Overtake Mode": +0.5 MJ when within 1 s at one detection point per lap. The archive has an `OvertakeSeries` topic.
- **`static/StreamingStatus.json`** returns `{"Status":"Offline"}` now; `static/SessionInfo.json` gives the current/last session and its status.
- **FastF1 auth**: the only public interfaces are the CLI `python -m fastf1 auth …` and `get_auth_token()`.
  - `get_auth_token()` opens a blocking browser/console flow when the token is invalid, so it is unsafe mid-feed.
  - The private helpers stay necessary.
- **Win+D**: the known approach is `SetParent` into the desktop WorkerW/Progman window. Windows 11 24H2 changed that structure; must be tested on the user's build 26200.

## Workflow per group
1. Research the open technical points listed for the group (official docs / FastF1 source / FIA). If still unclear: **ask the user, don't guess.**
2. Implement with the smallest diff that fits the current architecture. Keep network work off the UI thread, and keep the cache, IPv4-first and config compatibility.
3. Validate:
   - `pytest`
   - `python -m boxbox.sim` (Azerbaijan backtest; lap MAE must stay ≤ 0.36 s)
   - multi-race backtest (from group 2)
   - offscreen Bahrain replay smoke script
   - app launch check
4. Update `docs/CHECKLIST.md`, `docs/PLAN.md` (status) and `docs/TESTING.md` (manual test plan for the group), plus README sections touched.
5. Report Changed / Bugs fixed / Tests run (commands + output) / Metrics / Not tested / Limitations / Files. **Stop.**

Docs created at the start of group 1:
- `docs/PLAN.md` (this plan)
- `docs/CHECKLIST.md` (all 20 items + sub-tasks, ticked as done)
- `docs/TESTING.md` (per-group manual test steps)

Dev deps: `requirements-dev.txt` (pytest now, pyinstaller in group 5).

---

## Group 1 — Correctness
**1. Pit loss (sim.py, live.py, widgets.py)**
- `State` collects real stops from `PitStopSeries` into `state.stops` (one per drv+lap). The `pit` lap flag comes from real stops when that topic flows; otherwise it falls back to InPit/PitOut, ignoring lap 1 and grid/pit-lane starts.
- Neutralised-lap detection, replacing the TrackStatus-only check:
  - TrackStatus 4/5/6/7 seen during the lap.
  - RCM safety car / VSC / red flag messages (exact RCM fields to be checked against the archive first).
  - **Statistical check**: lap N is abnormal when the field median for lap N exceeds the session's green median by a calibrated ratio. This catches SC starts and formation laps that TrackStatus misses.
  - Lap ≤ 1 always excluded. The `sc` flag is set for all of these, which also cleans `clean()` and the model fit.
- `pit_loss_from(laps, stops, …)` returns `{'normal', 'sc', 'vsc', 'source', 'n'}`:
  - Per valid stop: `in-lap + out-lap − 2 × ref`, where ref = the driver's median clean lap within ±5 laps.
  - Valid stop: both laps green, lap > 2, not a drive-through (from `PitStopTime`).
  - Sanity bound from data: `0 < loss ≤ PitLaneTime`.
  - Result: median after MAD outlier rejection.
- Source order:
  1. MultiViewer `pitLoss` (incl. `vsc`)
  2. Session estimate (≥ 2 valid stops)
  3. Earlier session of the same weekend (sprint)
  4. MultiViewer value for the same circuit in a previous year
  5. Model default, **labelled `(default)` in the UI**
- SC/VSC loss: use the MultiViewer values, else observed SC stops, else the ratio knob (calibrated in group 2).
- The sim uses `vsc` loss on VSC laps. The panel shows the source.

**2. Sprint projected points (widgets.py `DriverStandings.projected`, data.py)**
- Primary source: the `ChampionshipPrediction` feed. Drivers via the DriverList number → TLA; teams via the feed's team names. Shows `PredictedPoints` and the delta against `CurrentPoints`. No double counting, sprint vs race handled by F1 itself. Constructor widget gets the same.
- Fallback when the feed is absent:
  - Compute from positions with the `POINTS` table for the session type.
  - "Already counted" comes from `data.round_results(round)`: does Jolpica `/current/{r}/results.json` or `sprint.json` have races? This replaces the `round` comparison and removes the ponytail note at widgets.py:141.

**3. Tests**
- `tests/test_pitloss.py`: synthetic laps for normal stop, lap-1 false flag, SC lap, VSC lap, slow opening laps (Bahrain shape), insufficient stops → labelled fallback, PitLaneTime bound.
- `tests/test_points.py`: feed prediction for race and sprint; fallback with mocked Jolpica (sprint counted, race not → race projection still shown).
- Integration tests marked `@pytest.mark.integration` (Bahrain archive), skipped by default.

**4. Revalidation**
- Bahrain lap-11 pit loss must be about 20–25 s, with a sensible rejoin and "What if".
- Azerbaijan MAE must not regress above 0.36.
- Script: `scripts/smoke_replay.py` (offscreen).

## Group 2 — Simulator (sim.py)
- **5. Per-car tyre choice.** Vectorised over (runs, cars, compounds):
  - Cost per allowed compound = laps_left·offset + deg·n(n−1)/2, plus an extra-stop penalty when life < laps_left.
  - Allowed set comes from the two-compound rule (waived once inter/wet is used) and the condition class.
  - Replaces the scalar `choice` at sim.py:170.
- **6. Wet/inter.**
  - `conditions` input per future lap (DRY/INTER/WET). Default: hold the current state, inferred from WeatherData `Rainfall` plus the share of the field on inters/wets. **No rain forecast is invented**; the UI can set "rain from lap X" as a what-if.
  - A tyre class not matching the condition forces a stop (removes `want &= ~wet`, sim.py:167).
  - Pace per condition is fitted from recent laps. The mismatch penalty is a documented knob.
- **7. Lapped rejoin.** `lap_s` = leader's median recent green lap from `state.laps`, then the model prediction, then 95 only as a last resort (logged). Applies to sim.py:117/198.
- **8. Overtaking.** Per-lap pass probability `sigmoid((Δpace − dirty_air + overtake_mode − threshold)/scale)` when within the gap:
  - Overtake Mode boost applies within 1 s (2026; older seasons use the DRS param).
  - Lapped cars yield (blue flags).
  - Pit/SC states respected.
  - Still O(D) per lap.
- **9. Calibration.** `python -m boxbox.calibrate` runs across N archived races (2026 + 2025):
  - Fits `sc_prob`/`sc_laps`/VSC rate from TrackStatus, SC/VSC pit-loss ratios from MultiViewer, and pass parameters from observed position changes.
  - Leave-one-race-out validation.
  - Writes `boxbox/calibration.json`, which config `model` overrides still beat.
  - Multi-race backtest table: lap MAE, rank correlation, rejoin error.

## Group 3 — Live / data
- **10. Auto-detect.** A `LiveWatcher` in main.py, with off-UI fetches:
  - Schedule-aware polling of `StreamingStatus.json` + `SessionInfo.json`: every 60 s inside [session start − 15 min, start + 3 h] from the Jolpica schedule, otherwise every 30 min. Semantics to be checked during a session window first.
  - Auto-starts `LiveFeed` once. If a replay is running, it only sends a tray notification. Stops when the session ends.
  - Tray toggle "Auto-start live tracker" (default on). The manual start is kept.
- **11. Positions.**
  - Use `trackPositionTime` (speed profile) for time → distance between timing points.
  - Calibrate real mini-sector boundaries from archived GPS + segment timing of an earlier session of the same circuit, cached per circuit.
  - Equal-length split stays as the last fallback in `Track.bounds`.
- **12. Replays.** Year submenus (2018 → now, archive start to be checked), lazy-loaded off-thread. Page size from settings plus a "More…" item. Past-year indexes cached forever, current year 10 min.
- **13. Qualifying.** Tower quali mode for Qualifying / Sprint Qualifying:
  - Q/SQ segment label and countdown from `ExtrapolatedClock` (added to `ReplayFeed.TOPICS`).
  - Best time in the current part.
  - Knocked-out drivers greyed; danger zone from `NoEntries` / `Cutoff`.
- **14. Replay projected points.** `ChampionshipPrediction` is added to `ReplayFeed.TOPICS`, and the projection is shown for replays (labelled as of that session). Fallback is computed per year (fastest-lap point only for 2019–2024).

## Group 4 — UX
- **15. Settings dialog.** QDialog + QFormLayout with range-limited spin boxes: sim runs, SC/VSC probability, tyre life/priors, overtaking params, calibration source, replay page size, auto-live.
  - Saves to the existing `cfg['model']` keys and applies immediately (`App.model()` is already read per run).
  - Replaces the `open_config` JSON hack (main.py:227).
- **16. Desktop layer.** Research and test the WorkerW/Progman `SetParent` via ctypes on build 26200, keeping dragging, resizing and saved positions. If Windows blocks it: explain, keep on-top mode, and offer a re-show-after-Win+D fallback. **Ask the user if neither works acceptably.**
- **17. Logging.** `boxbox/log.py`:
  - `RotatingFileHandler` to `%APPDATA%\boxbox\logs\boxbox.log` (1 MB × 5), plus a console handler when not frozen.
  - `bg()` logs exceptions with tracebacks. The prints in live.py are replaced.
  - Tokens are never logged.
- **(15-auth)** All FastF1 private auth calls move into one `boxbox/auth.py` with try/except and clear user messages, so the app does not crash if FastF1 internals change. The live feed avoids `get_auth_token()`'s blocking re-auth by checking token expiry before connecting.

## Group 5 — Distribution
- **18. requirements.txt.** Direct deps only: `fastf1==3.8.3` (pinned for the private API), PySide6, numpy, scipy, requests, with tested lower bounds.
- **19. Test suite expansion.**
  - Data cache (fresh, stale refresh, offline → stale fallback, mocked `requests`).
  - Sim (deg, tyre changes, SC, VSC, wet, two-compound rule, race completes).
  - Replay (starts, speed timing, finishes) with fake message lists.
- **20. Start with Windows.** HKCU `...\CurrentVersion\Run` value via stdlib `winreg` (no admin); `pythonw -m boxbox.main` or the frozen exe path. Off by default, tray/settings toggle.
- **21. Packaging.**
  - PyInstaller one-dir windowed `boxbox.spec` + `build.ps1`.
  - Research FastF1/PySide6/signalrcore hidden imports, and the browser-auth callback in a frozen build.
  - Build and launch the exe. Config/cache/logs stay in `%APPDATA%\boxbox`.

## Critical files
- `boxbox/sim.py`: pit_loss_from, simulate, rejoin, gap_s, MODEL, backtest
- `boxbox/live.py`: State._timing/_lap/apply, ReplayFeed.TOPICS, Track.bounds, load_track, sign-in
- `boxbox/widgets.py`: DriverStandings.projected, SimPanel.loss/tick, TimingTower, TrackMap
- `boxbox/main.py`: App menu, config, timers
- `boxbox/data.py`: Jolpica helpers, past_sessions, circuit
- New: `tests/`, `scripts/smoke_replay.py`, `boxbox/calibrate.py`, `boxbox/log.py`, `boxbox/auth.py`, `docs/*.md`, `requirements-dev.txt`, `boxbox.spec`, `build.ps1`

## Verification (every group)
- `.venv\Scripts\python -m pytest -q` (unit; `-m integration` for archive tests)
- `.venv\Scripts\python -m boxbox.sim`: Azerbaijan lap MAE ≤ 0.36, rejoin, rank correlation
- From group 2: `python -m boxbox.calibrate --report` (multi-race metrics table)
- `PYTHONPATH=. python scripts/smoke_replay.py`: Bahrain lap-11 pit loss, rejoin, what-if, tower, map, standings
- Launch `python -m boxbox.main` for real and confirm the tray and widgets come up, then quit.
- Untestable now, to be listed each time: live session (next is Singapore FP1, Oct 9), F1 TV login, Win+D on other Windows builds.
