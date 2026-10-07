# Singapore GP live test (9–11 Oct 2026)

First real live test of the app. Times are India time (IST), from the Next race widget.

**Before Friday:** quit old copies (tray → Quit, close F1 Live). Start fresh from the desktop icons.
The dashboard should say `F1 TV: signed in ✓` (if not, Sign in to F1 TV… again).

Tick boxes as you go, and note anything odd with a screenshot and the time it happened.

---

## Fri 09 Oct, 14:00 — FP1 (most of the test can be done here)
Open **F1 Live** ~5 min before the start.
- [ ] Status line (top right) says `live (F1 TV)` and the session name changes to Singapore Practice 1
- [ ] Timing table fills within seconds of cars going out; lap times update
- [ ] Map footer is green `GPS`; cars move smoothly round the track, pit-lane cars grey
- [ ] Map border changes colour only for SC (yellow), VSC (orange) or a red flag (red), not for local yellow flags
- [ ] Leave it running through the whole session: it keeps updating (no freeze, no "disconnected" that stays)
- [ ] Desktop widgets: Win+D keeps them visible; double-clicking F1 Tracker / F1 Live again doesn't open a second copy
- [ ] Settings… → Delay live data: find the delay that matches the F1 TV video (note it down)
- [ ] Wind compass shows; SC dot appears only if the safety car goes out
- [ ] F1 Live shows the practice layout: sector timing (mini-sectors fill in), Tyres, Race control

## Fri 09 Oct, 18:00 — Sprint Qualifying
- [ ] Standings widgets (tray app) must **not** say `LIVE PROJECTION` (this was a bug)
- [ ] F1 Live: header shows `SQ1` → `SQ2` → `SQ3` and the time left; drop zone red, knocked-out drivers grey
- [ ] Mini-sector squares and sector times fill in live

## Sat 10 Oct, 14:30 — Sprint (first test of points projection)
- [ ] Driver standings: `DRIVERS · LIVE PROJECTION`, green `+8 … +1` for the top 8
- [ ] Constructor standings: `CONSTRUCTORS · LIVE PROJECTION`
- [ ] Sim panel: lap count, pit loss `27.6s (circuit data)`, "Pit now → rejoin P…" looks sensible
- [ ] Click **What if** once: finishes in a few seconds

## Sat 10 Oct, 18:30 — Qualifying
- [ ] Same as FP1: timing + GPS map keep working

## Sun 11 Oct, 17:30 — Race (the original bug + pit stops)
- [ ] **Before the start**, note one driver's total points. During the race it should be that total plus their race projection (sprint points counted once, not twice)
- [ ] Projection shows `+25`, `+18`, … and moves when positions change
- [ ] When cars pit: the timing table shows PIT; sim panel's pit loss stays `27.6s (circuit data)`
- [ ] Under SC/VSC (common in Singapore): map border yellow/orange, sim panel says `… under SC` / `under VSC`
- [ ] While an SC/VSC is out, What if / Best window results reflect it (cheap stops now, field closed up after)
- [ ] What if / Best window run during the race; try "safety car on lap N" and check the scenario line above the result
- [ ] Tray timing tower in qualifying (Sat 18:30): Q1/Q2/Q3 label, time left, drop zone red, knocked-out cars grey with no PIT

## Also this weekend
- Item 10 (automatic live-session detection) gets built and tested on these sessions.
- After the race: refit the 2026 sim (`python -m f1tracker.calibrate 2026 --write`).

## After the weekend — send me
- `%APPDATA%\f1tracker\live.txt`: raw live recording (shows whether pit-stop times arrive live with F1 TV)
- `%APPDATA%\f1tracker\live.log` and `tray.log`: any errors
- Screenshots of anything that looked wrong, with the session and lap

Paste `%APPDATA%\f1tracker` into the Explorer address bar to open that folder.
