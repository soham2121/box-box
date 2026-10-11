# Manual test plan

**Live race-weekend test (Singapore, 9–11 Oct): [SINGAPORE_LIVE_TEST.md](SINGAPORE_LIVE_TEST.md)**

Each group adds a section. Run the automated checks first, then the manual steps. Write down anything that looks
off (screenshot + lap number helps).

## 0. One-time setup

```
cd D:\Projects\F1
python -m venv .venv            (already done if .venv exists)
.venv\Scripts\pip install -r requirements.txt -r requirements-dev.txt
```

Start the app: `.venv\Scripts\python -m boxbox.main`. A red **F1** icon appears in the tray (may be under the `^`
overflow arrow). Everything is in its right-click menu.

---

## Group 1 — Correctness (pit loss, projected points)

### A. Automated (≈ 2 min)

| Command | Expected |
|---|---|
| `.venv\Scripts\python -m pytest` | `23 passed, 2 deselected` |
| `.venv\Scripts\python -m pytest -m integration` | `2 passed` (needs internet) |
| `.venv\Scripts\python -m boxbox.sim` | `lap_mae_s` ≈ 0.356, `backtest OK` |
| `.venv\Scripts\python scripts\smoke_replay.py` | ends with `smoke OK`; lap 11 shows `pit loss 22.7s (typical circuit…)`, lap 25 `(this session, n=4)` |

### B. Basic app check (≈ 3 min)
1. Start the app. Driver standings, constructor standings and next race widgets appear.
2. Drag a widget, resize it with the corner grip, quit (tray → Quit), restart: position and size are kept.
3. Tray → Rows shown → Drivers → Top 5: the widget shows 5 rows.
4. Next race widget counts down every second; session times are in your local time.

### C. Pit-loss fix on the Bahrain replay (≈ 10 min)
1. Tray → Replay a past session → 2026 → Bahrain Grand Prix → Race. Track map and timing tower open.
2. Tray → Strategy sim (tick it) to open the sim panel. Tray → Replay speed → 30x.
3. Pick any driver in the sim panel's drop-down.
4. While laps 2–14 run, the panel's first line should read `pit loss 22.7s (typical circuit)`.
   - **Before the fix** it said ~105 s. Anything above ~30 s here is a bug.
   - While the SC is out (laps 9–12, map border yellow) it should also say `, 14.4s under SC`.
   - "Pit now → rejoin P…" should be a few places back, not P19.
5. After laps ~20–25 it should switch to `(this session, N stops)` with a value around 26–29 s.
   - That is genuinely higher than the usual ~22 s: this venue's pit lane takes 27.6–29 s to drive through
     (Baku: ~20.7 s). Over the whole race the estimate settles around 27 s from ~20 stops.
6. During the VSC (around lap 43) the line should show `under VSC`.
7. Click **What if** with pit lap a few laps ahead and HARD. It should finish in a few seconds and print two rows
   ("Current plan" / "Pit L… HARD").
   - **Known limitation, fixed in group 2:** gains from switching compound can be overstated (e.g. "+6 places")
     because the sim still picks one tyre for the whole field. Judge only that it runs and doesn't say "P19"/
     "loses 12.98 places" under normal conditions.

### D. Azerbaijan replay (MultiViewer has data)
1. Tray → Replay → 2026 → Azerbaijan Grand Prix → Race, open the sim panel.
2. The panel should read `pit loss 21.8s (circuit data)` from the first lap.

### E. Projected points — what can be tested now
Live projection only runs with a **live** session (replay projection is group 3, item 14).

- Next live chance: **Singapore GP sprint weekend**.
  - Sprint Qualifying, Fri 09 Oct 18:00 (your time per the next-race widget): the app goes live by itself (tray tooltip `LIVE`). Standings must
    **not** show `LIVE PROJECTION` (old bug: it projected sprint points in sprint quali).
  - Sprint, Sat 10 Oct: standings show `DRIVERS · LIVE PROJECTION` with green `+8 … +1` deltas for the top 8;
    constructors show `CONSTRUCTORS · LIVE PROJECTION`.
  - Race, Sun 11 Oct: **this is the original bug.** After the sprint has been scored, the race projection must
    still show (`+25`, `+18`, …) and the totals must include the sprint points exactly once (compare a driver's
    total before the race start with the sprint result).
- Offline check of the same logic: `.venv\Scripts\python -m pytest tests/test_points.py -v`.

### E2. Start with Windows + positions after a restart (done early)
1. Double-click `Box Box.bat`. The tray icon appears, with no console window.
2. Drag a widget somewhere new and wait 2 s.
3. Tray → tick **Start with Windows**.
4. Restart the PC, without quitting the app first. After login the tray icon and the widgets come back, at the new position.
5. Tray → untick **Start with Windows**, restart again: the app should not start.

### F. Not testable yet
- Live feed behaviour (needs a live session; next is Singapore FP1, Fri 09 Oct).
- F1 TV sign-in (needs your F1 TV account).
- Pit loss "from sprint" source (needs a sprint weekend at a circuit MultiViewer lacks).

---

## Live dashboard (added 2026-10-06)

### A. Automated
| Command | Expected |
|---|---|
| `.venv\Scripts\python scripts\smoke_dashboard.py` | `live on launch: LiveFeed …`, tower with INT/LEADER/LAST/BEST columns, a what-if result, `live threads alive: 0` |

### B. Between race weekends (now)
1. Double-click **Box Box** on the desktop. The dashboard opens: map left, timing right, sim bottom right.
2. The status line (top right) shows `live (no auth: estimated map) · <last session> … Ends`: F1's server sends the last session's final state.
3. Replay → 2026 → Bahrain Grand Prix → Race, then Replay → Speed → 30x.
   - Laps 9–12: yellow map border, cars in the pit lane grey, tower shows PIT, sim says `under SC`.
   - Drag the splitters to resize the panels. Close and reopen: size and splitters are kept.
4. Tray → Open live dashboard brings the same window to the front (there is only one). Close it: the tray and widgets carry on.

### C. F1 TV sign-in
1. Click **Sign in to F1 TV…**. Your browser opens the F1 login; the button says `Finish the sign-in in your browser…`.
2. After logging in it says `F1 TV: signed in ✓`.
3. During a session: the map footer turns green `GPS`, the status shows `live (F1 TV)`.
4. Cancel/close the browser instead: after 5 min the button says `Sign-in did not finish, try again…`. The app keeps running.

### D. Live (Singapore weekend, Fri 09 – Sun 11 Oct)
1. Open the dashboard a few minutes before FP1. When the session starts, the timing fills within seconds, and the map moves (estimated, or GPS if signed in).
2. Sprint / race: the sim panel shows the pit loss source changing as stops happen (Singapore has circuit data: expect `27.6s (circuit data)`).
3. Leave it running across a red flag or long gap: it should keep or regain the connection (it reconnects 15 s after a drop).
4. After the session, send me `%APPDATA%\boxbox\live.txt` (raw feed) so I can check which data arrived with F1 TV.

---

## Desktop icons + split opacity (added 2026-10-06)
1. Your desktop has **Box Box** with the red F1 icon. Double-click it: the tray icon appears and the dashboard opens. No console window.
2. Tray → **Text opacity** → 55%: the text fades, the dark background stays.
3. Tray → **Background opacity** → 0%: the background disappears and only text shows. You can still drag the widget by grabbing anywhere inside its area.
4. Restart the app: both settings are kept.
5. Automated: `.venv\Scripts\python -m pytest` → `23 passed`.

---

## Win+D + single instance (added 2026-10-06)
First quit the tray app you already have running (tray → Quit): it still runs the old code.

1. Start **Box Box** from the desktop. Widgets appear behind your open apps.
2. Press **Win+D**. All widgets stay visible on the desktop.
3. Press **Win+D** again (or click any app): your apps come back over the widgets.
4. Click the desktop wallpaper: the widgets come forward again. Drag one: it moves normally.
5. Tray → **Always on top**: the widgets stay above everything. Untick it: back to the desktop behaviour.
6. With the app running, double-click **Box Box** again. No second set of widgets appears; the dashboard opens (or comes to the front).
7. Tray → Quit, then double-click **Box Box**: it starts normally.

---

## F1 TV sign-in + one dashboard (fixed 2026-10-06)
First quit the old copies: tray → Quit, and close the F1 live window.

1. Open the dashboard (desktop **Box Box**). Click **Sign in to F1 TV…**.
2. If the page says "Get the FastF1 Companion Extension": install it, then click **Sign in to F1 TV…** again. It reuses the same waiting login, so you don't need to close anything.
3. The extension sends you to account.formula1.com. Log in; it then shows "Login Successful!". Click **Connect FastF1**.
   - Expected: "Authentication successful!" in the browser, and `F1 TV: signed in ✓` in the dashboard.
   - Not expected any more: "Could not connect to the local FastF1 application".
4. If it fails, the dashboard button shows why (timeout, no F1 TV subscription, token didn't verify). Send me `%APPDATA%\boxbox\logs\boxbox.log` and a screenshot.
5. Double-click **Box Box** again: no second window opens; the open one comes to the front.

---

## Replay jumping (added 2026-10-06)
1. Dashboard → Replay → 2026 → Bahrain Grand Prix → Race. A second toolbar row appears.
2. Drag the slider to about 3/4: within a second the map, timing and sim jump to about lap 41; the label shows `L41/55 · …`.
3. Click **◀ Lap** three times quickly: you land 3 laps back. **Lap ▶** goes forward one.
4. **Pause**: everything freezes, the map keeps showing GPS positions. **Play** resumes at the previous speed.
5. Jump back to lap 5: the sim panel's laps-logged count drops; the pit loss goes back to `typical circuit`.
6. Pick a practice session: the buttons say `◀ 1 min` / `1 min ▶`.
7. Tray app: Replay a past session → … → tray menu shows **Jump to lap…**; enter 30.

## Dashboard settings, SC, wind (added 2026-10-06)
Restart the app first (tray → Quit, then **Box Box**).
1. Dashboard → Replay → 2026 → Bahrain Grand Prix → Race. Drag the slider to about lap 11 (SC period). A yellow `SC` dot leads the field. The status line says `SC Deployed` with a space.
2. Jump to lap 20 (green flag). The SC dot is gone.
2b. Drag the slider near the end (after lap 50): BOT, ALB and RUS have no dots on the map (they stopped) and are struck through in the timing table.
3. Top right of the map: a compass with `N`, a blue arrow and `wind … m/s from …`.
4. **Settings…** → driver names 150 %: names grow straight away.
5. In the list, click **None**, then tick two drivers. Only those two have names; all dots stay. **All** brings every name back.
6. Close and reopen the dashboard: its size and the picked drivers are kept.
7. **Live (Singapore):** watch F1 TV in the browser. Set **Delay live data** until an overtake shows on the map when you see it on the video (try 30–60 s first). The status line shows `delayed N s`. Note the number that works for you.

## Session layouts (added 2026-10-06)
Close and reopen the dashboard first.
1. Replay → 2026 → Bahrain Grand Prix → Qualifying. Drag to about 3/5 (Q2).
   - The right side shows sector timing, with Tyres and Race control below.
   - The header says `Q2 · mm:ss left` and counts down.
   - Positions 11–16 are red; 17–22 are grey with `out`.
2. Watch a car on a flying lap: its mini-sector squares fill in yellow/green/purple, and the S1/S2/S3 times appear as each sector ends.
3. Race control: newest message first, coloured bar for flags (blue flags, black and white, green light…).
4. Replay → 2026 → Bahrain Grand Prix → Race. The right side switches to the race layout.
   - Click **Tyre stints**: coloured bars per driver. HAM shows `S31` then `M…*`.
   - Click **Race control** in the bottom area.
5. Close and reopen: the same tabs are selected.
6. Replay a Practice session: same layout as qualifying, no Q label, `mm:ss left` in the header.
7. **Live (Singapore):** FP1 uses the practice layout, Sprint Qualifying shows `SQ1/SQ2/SQ3`, and the Sprint and Race switch to the race layout on their own.

## Replay speed setting + sim row (added 2026-10-06)
1. Dashboard → **Settings…** → **Replays start at** 30x. Start a replay: it plays at 30x and the speed box says 30x. Pause → Play resumes at 30x.
2. Restart the app: the setting is kept.
3. Race replay, Strategy sim tab: the row reads `driver ▾ · pit on lap · [lap] · compound ▾`, with no big gap after the label.

## Older seasons in the Replay menu (added 2026-10-06)
1. Dashboard → **Replay**: years 2026 down to 2018 are listed. Hover **2024**: it says `loading…` for a second or two, then lists 25 weekends from Pre-Season Testing to Abu Dhabi. Each opens to the side with its sessions.
2. 2018 → Australian Grand Prix → Race: it plays. The map says `ESTIMATED` (no GPS for that race). Tyres: `US` purple, `SS` red, `S` yellow (2018 colours).
   2018 → Abu Dhabi Grand Prix → Race, Tyre stints tab: `HS` pink, `US` purple, `SS` red bars.
3. 2023 → Qatar Grand Prix: it lists Practice 1, Qualifying, Sprint Shootout, Sprint and Race; the Race plays.
4. 2022 lists every weekend (from FastF1's mirror). 2022 → Bahrain Grand Prix → Race plays with GPS.
5. Tray → Replay a past session (and its `…without GPS` submenu) has the same year → weekend → session menus.
6. `.venv\Scripts\python -m pytest -m integration -k replay_years` passes (network).

## Group 2: strategy sim (added 2026-10-06)
1. `.venv\Scripts\python -m pytest` passes (`tests/test_sim.py`: lapped gaps, two-compound rule GP vs sprint, wrong tyres in rain, pass odds, planned stop then rain).
2. `.venv\Scripts\python -m boxbox.calibrate 2026` (about a minute) ends with a `calibrated` line better than `default` on pos_err and logp. Without a year it fits all three eras (about an hour the first time).
2b. Strategy sim on a 2019 replay (2019 → Italian Grand Prix → Race, lap 25): the mass line says about 796 kg (743 kg minimum + 53 kg of the 100 kg start fuel left), not a 2026 figure.
3. `.venv\Scripts\python -m boxbox.sim` still prints `backtest OK` (Azerbaijan).
4. Dashboard → Replay → 2026 → Bahrain Grand Prix → Race, jump to lap 25. Strategy sim tab: under the pit row, pick **rain (inters) from lap** 30, then **Best window**: the list is INTERMEDIATE stops around laps 29–31. Back to **track stays as now**: dry compounds again.
5. Same replay, a lapped car in the driver box: "Pit now → rejoin" shows a position (it used to fail once anyone was lapped).
6. A sprint replay (2026 → Miami Grand Prix → Sprint): What if for a car that has only used one compound no longer forces a stop.

## Group 3: replay projection, tray qualifying, estimated positions (added 2026-10-06)
1. Tray → Replay a past session → 2018 → Abu Dhabi Grand Prix → Race, jump to the last lap. Driver standings header: `PROJECTION AS OF 2018 REPLAY`, HAM 408 (+25), VET 320; constructors Mercedes 655.
2. Tray replay 2021 → British Grand Prix → Sprint Qualifying near the end: VER +3, HAM +2, BOT +1 (2021 sprint points).
3. Tray replay 2026 → Bahrain Grand Prix → Qualifying, slider to about 3/5 (Q2): the timing tower header shows `Q2 · mm:ss left`, P11–16 red, six cars greyed `out`.
4. Tray → Replay a past session → …without GPS → 2026 → Azerbaijan Grand Prix → Race. Within a few seconds `%APPDATA%\boxbox\logs\boxbox.log` says `estimated positions calibrated on …Qualifying`. The estimated dots now sit on the cars' real spots (Baku median error 8 m, was 126 m behind).

## SC / VSC / red flag in the sim (added 2026-10-06)
1. Dashboard → Replay → 2026 → Bahrain Grand Prix → Race, lap 25. Strategy sim: third row **safety car on lap** 30, **Best window**: the result says `with safety car on lap 30`; the window ends at the SC (stops on laps 31–33, after the queue has formed, rank lower). Scroll the result if it's long.
2. Same, **What if** (pit on lap 28): the result also starts with `with safety car on lap 30`.
3. Jump into the lap-9 SC of that race (laps 9–12), What if: the result says `SC out now (~N laps left)`; after race control's "SAFETY CAR IN THIS LAP" it says ~1 lap.
4. **red flag on lap** N: cars that owe a stop no longer have to pit later (free tyre change).
5. `.venv\Scripts\python scripts\validate_pit_neutral.py` (about half an hour) prints, per era, the worn rule vs the fitted odds vs + shared factor on seasons the fit never saw; the shared factor's "spread ratio" is near 1.

## Dashboard look, monitors, live countdown (added 2026-10-09)
1. Dashboard → Replay → 2026 → Bahrain Grand Prix → Race, lap 9. Toolbar chips: the feed, session, lap, and the track status chip in yellow while the SC is out. The fastest lap in the timing tower is purple; the sim's What if shows a green bar of the likeliest finishes.
2. With a second monitor: drag a tray widget onto it, quit, unplug the monitor (or move it in Windows display settings), start again. The widget sits on the nearest screen's edge instead of off screen. Same while the app runs: about half a second after the change.
3. During a session (Singapore FP1, Fri 9 Oct): the Next race widget shows `FP1 LIVE` in green instead of the countdown, then the countdown to the next session an hour after the start.
4. GitHub: open the repo page. The README banner animates (cars lap Marina Bay, five lights come on, then go out).

## One app, battles, alerts, Box Box (added 2026-10-11)
Quit the old app first (tray → Quit), then start **Box Box** from the desktop. The first start moves `%APPDATA%\f1tracker` to `%APPDATA%\boxbox` and renames the Start with Windows entry.

### A. Automated
`.venv\Scripts\python -m pytest` (85 pass), `-m integration` (3 pass, downloads data), `.venv\Scripts\python scripts\smoke_replay.py`, and `.venv\Scripts\python scripts\smoke_dashboard.py`: goes live by hand, replays Bahrain to lap 25 while live keeps recording, what-if, back to live, closes the dashboard (tray and live carry on).

### B. Between race weekends
1. Double-click **Box Box**: tray icon and dashboard. Double-click it again: the dashboard comes to the front, no second tray icon. Close the dashboard: the tray icon stays; hover it for `next: … in …`.
2. Tray → **Settings…**: tick **Follow** for two drivers and **Alerts** for one; pick **My team**. Replay Bahrain 2026 race (dashboard → Replay): followed drivers have a white ring and white name on the map, every other car keeps its name; their timing rows are highlighted; the strategy sim opens on the first.
3. **Battles** tab (top right, races only): a card per followed driver with the car ahead and behind: on track gap, net of stops, pace, tyres, gap chart. **+ Battle** pins a pair; right-click the timing tower → **Add battle** does too; **✕** removes it.
4. Strategy sim tab: pit loss reads `this session, N stops` once 3 stops are done (no longer the published value).
5. The map has no telemetry card.
6. Tray → **Strategy sim accuracy** lists refits (empty until the first race after this build reaches the archive).
7. Settings → **Strategy sim**: each knob shows `fitted` and its fitted value in the label. Set soft tyre life to 18, run **Best window**: softs stop earlier. Turn it back down to `fitted`, or press **all back to fitted**.
8. Settings → **Go live by itself around every session** unticked: the tray tooltip says `(going live by itself is off)`; tick it again.
9. `%APPDATA%\boxbox\logs\boxbox.log` exists and has a `started` line with the time; tray menu → **Sign in to F1 TV…** shows the sign-in's state and expiry date.

### C. Next race weekend (live)
0. Before the weekend: tray menu → **Sign in to F1 TV…** shows `signed in to F1 TV until …` after a date past the race. If not, sign in again (the tray also warns when it would run out first).
1. Leave the app running (or Start with Windows). From 10 min before FP1 the tray icon gets a green dot and the tooltip says `LIVE · …` without clicking anything; widgets show the session. If a session overruns (red flag), live stays on until it's finished, past the 30 min.
2. Open the dashboard during the race: **⏱ Sync delay** (or S) the moment the F1 TV lap counter changes → a popup says the new delay; overtakes on the map now match the stream.
3. Alerts: with the dashboard open they pop up top right; minimise it and they come as Windows notifications. Expect pit stops of cars around your alert driver, SC/VSC, investigations.
4. Close the dashboard mid-race and reopen it: laps logged so far are all there (sim panel `N laps logged`).
5. 30 min after the flag the green dot goes. A few hours later the tray says a sim refit is ready: tray → **Apply new sim fit**.
