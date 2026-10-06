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

The app starts as a tray icon (red **F1**). Right-click it for everything else.

## Widgets

| Widget | Shows |
|---|---|
| Driver standings | Top N drivers; during a live race/sprint, projected points if it finished now |
| Constructor standings | Top N teams |
| Next race | Weekend sessions in your local time, countdown to the next one |
| Track map | Car positions, safety car, SC/VSC/red flag border |
| Timing tower | Position, interval, last lap, tyre + age, stops, pit status |
| Strategy sim | Pit rejoin position, "pit on lap N for compound C" vs current plan, best stop window |

Drag widgets to move them, use the corner grip to resize. Tray menu: toggle widgets, rows shown, opacity, lock positions, always-on-top vs desktop layer, refresh interval.

## Live sessions

Tray → **Live tracker** during any session.

- **Without F1 TV:** timing, tyres and gaps are live; car positions on the map are estimated from timing (typically within 100–200 m).
- **With F1 TV:** tray → **Sign in to F1 TV…**, log in in the browser, then restart the live tracker for real GPS positions.

Tray → **Replay a past session** plays any finished 2026 session through the same widgets (speed adjustable), so you can try everything between race weekends.

## Strategy sim knobs

Model settings live in `%APPDATA%\f1tracker\config.json` under `"model"` (tray → **Edit sim model knobs**), e.g.:

```json
"model": {"fuel_start_kg": 70, "pass_threshold": 1.0, "sc_prob": 0.4}
```

Restart the app after editing. Defaults and their meanings are in `MODEL` at the top of `f1tracker/sim.py`.

Backtest the lap-time model against a past race:

```
.venv\Scripts\python -m f1tracker.sim
```

## Data sources

- [Jolpica-F1](https://github.com/jolpica/jolpica-f1): standings and schedule
- F1 live timing (via [FastF1](https://github.com/theOehrly/Fast-F1)): live data and the session archive
- [MultiViewer](https://multiviewer.app) circuit API: track outlines and pit loss

Not affiliated with Formula 1. Responses are cached in `%APPDATA%\f1tracker\cache`.
