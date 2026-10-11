"""Static data: Jolpica standings/schedule, MultiViewer circuit info, F1 live timing archive. All disk-cached."""
import hashlib
import json
import os
import socket
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

# Networks with broken IPv6/NAT64 stall ~20 s per connection before falling back to IPv4; curl hides it, Python doesn't.
_gai = socket.getaddrinfo
socket.getaddrinfo = lambda *a, **k: sorted(_gai(*a, **k), key=lambda r: r[0] != socket.AF_INET)

_ROOT = Path(os.getenv('APPDATA', Path.home()))
APP_DIR = _ROOT / 'boxbox'
if not APP_DIR.exists() and (_ROOT / 'f1tracker').exists():  # settings, recordings, caches of the app's old name
    try:
        (_ROOT / 'f1tracker').rename(APP_DIR)
    except OSError:  # files still open (the old app is running): keep using the old folder, moved next start
        APP_DIR = _ROOT / 'f1tracker'
CACHE = APP_DIR / 'cache'
UA = {'User-Agent': 'boxbox/0.1'}  # MultiViewer rejects the default python-requests agent
JOLPICA = 'https://api.jolpi.ca/ergast/f1'
ARCHIVE = 'https://livetiming.formula1.com/static/'
MIRROR = 'https://livetiming-mirror.fastf1.dev/static/'  # FastF1's mirror of the archive (fastf1._api.base_url_mirror)

TEAM_COLOURS = {  # Jolpica constructorId -> live timing TeamColour
    'mercedes': '00D7B6', 'ferrari': 'ED1131', 'mclaren': 'F47600', 'red_bull': '4781D7', 'rb': '6C98FF',
    'alpine': '00A1E8', 'haas': '9C9FA2', 'audi': 'F50537', 'sauber': '01C00E', 'williams': '1868DB',
    'aston_martin': '229971', 'cadillac': '909090',
}


def streaming():
    """F1's own "a session is streaming now" flag (StreamingStatus.json: 'Available' / 'Offline'), for sessions
    the schedule doesn't have or has at the wrong time."""
    r = requests.get(ARCHIVE + 'StreamingStatus.json', headers=UA, timeout=10)
    r.raise_for_status()
    return json.loads(r.content.decode('utf-8-sig')).get('Status') == 'Available'


def fetch(url, max_age=None, raw=False):
    """GET through a disk cache. max_age=None caches forever (history never changes). Offline -> stale cache."""
    f = CACHE / hashlib.md5(url.encode()).hexdigest()
    if f.exists() and (max_age is None or time.time() - f.stat().st_mtime < max_age):
        b = f.read_bytes()
    else:
        try:
            r = requests.get(url, headers=UA, timeout=30)
            if r.status_code >= 400 and url.startswith(ARCHIVE):  # FastF1's fallback; F1 refuses most of 2022
                r = requests.get(MIRROR + url[len(ARCHIVE):], headers=UA, timeout=30)
            r.raise_for_status()
            b = r.content
        except requests.RequestException:
            if not f.exists():
                raise
            b = f.read_bytes()
        else:
            CACHE.mkdir(parents=True, exist_ok=True)
            f.write_bytes(b)
    b = b.decode('utf-8-sig')
    return b if raw else json.loads(b)


def _standings(kind, at='current', max_age=1800):
    lists = fetch(f'{JOLPICA}/{at}/{kind}.json?limit=100', max_age)['MRData']['StandingsTable']['StandingsLists']
    return lists[0][kind[0].upper() + kind[1:]] if lists else []


def _code(drv):
    return drv.get('code') or drv['familyName'][:3].upper()


def driver_standings(at='current', max_age=1800):
    """at: 'current', or 'YEAR/ROUND' for the table after that round."""
    return [{'pos': i, 'code': _code(s['Driver']), 'name': f"{s['Driver']['givenName']} {s['Driver']['familyName']}",
             'team': s['Constructors'][-1]['name'], 'colour': TEAM_COLOURS.get(s['Constructors'][-1]['constructorId'], '888888'),
             'points': float(s['points']), 'wins': int(s['wins'])}
            for i, s in enumerate(_standings('driverStandings', at, max_age), 1)]


def constructor_standings(at='current', max_age=1800):
    return [{'pos': i, 'name': s['Constructor']['name'], 'colour': TEAM_COLOURS.get(s['Constructor']['constructorId'], '888888'),
             'points': float(s['points']), 'wins': int(s['wins'])}
            for i, s in enumerate(_standings('constructorStandings', at, max_age), 1)]


def standings_before(year, race_date, grand_prix):
    """Both tables as they stood before one points session of a past weekend (replay projection): after the previous
    round, plus the weekend's sprint when the session is its Grand Prix. race_date: the weekend's race day
    'YYYY-MM-DD' (the archive folder's date). None when Jolpica doesn't know the weekend."""
    age = None if year < datetime.now(timezone.utc).year else 1800  # finished seasons never change
    races = fetch(f'{JOLPICA}/{year}.json?limit=100', age)['MRData']['RaceTable']['Races']
    rnd = next((int(r['round']) for r in races if r['date'] == race_date), None)
    if rnd is None:
        return None
    drivers = driver_standings(f'{year}/{rnd - 1}', age) if rnd > 1 else []
    teams = constructor_standings(f'{year}/{rnd - 1}', age) if rnd > 1 else []
    sprint = fetch(f'{JOLPICA}/{year}/{rnd}/sprint.json?limit=100', age)['MRData']['RaceTable']['Races'] if grand_prix else []
    for res in (sprint[0].get('SprintResults', []) if sprint else []):
        p, code, team = float(res['points']), _code(res['Driver']), res['Constructor']['name']
        d = next((d for d in drivers if d['code'] == code), None)
        if not d:
            drivers.append(d := {'code': code, 'name': f"{res['Driver']['givenName']} {res['Driver']['familyName']}",
                                 'team': team, 'colour': TEAM_COLOURS.get(res['Constructor']['constructorId'], '888888'),
                                 'points': 0.0, 'wins': 0})
        t = next((t for t in teams if t['name'] == team), None)
        if not t:
            teams.append(t := {'name': team, 'colour': d['colour'], 'points': 0.0, 'wins': 0})
        d['points'] += p
        t['points'] += p
    return {'drivers': sorted(drivers, key=lambda d: -d['points']), 'teams': sorted(teams, key=lambda t: -t['points']),
            'round': rnd}


SESSION_KEYS = [('FirstPractice', 'FP1'), ('SecondPractice', 'FP2'), ('ThirdPractice', 'FP3'),
                ('SprintQualifying', 'Sprint Quali'), ('SprintShootout', 'Sprint Quali'), ('Sprint', 'Sprint'),
                ('Qualifying', 'Quali')]


def _utc(d):
    return datetime.fromisoformat(f"{d['date']}T{d.get('time', '00:00:00Z').replace('Z', '+00:00')}")


def schedule():
    races = fetch(f'{JOLPICA}/current.json?limit=100', 6 * 3600)['MRData']['RaceTable']['Races']
    return [{'round': int(r['round']), 'name': r['raceName'], 'circuit': r['Circuit']['circuitName'],
             'place': f"{r['Circuit']['Location']['locality']}, {r['Circuit']['Location']['country']}",
             'sessions': sorted([(label, _utc(r[k])) for k, label in SESSION_KEYS if k in r] + [('Race', _utc(r))],
                                key=lambda s: s[1])}
            for r in races]


# ponytail: scheduled lengths (Jolpica has start times only). A quali red flag that runs over shows the next countdown
# early; read SessionStatus from the live feed if that matters. Race: 3 h, the cap including red flags.
SESSION_MINUTES = {'FP1': 60, 'FP2': 60, 'FP3': 60, 'Sprint Quali': 45, 'Sprint': 60, 'Quali': 60, 'Race': 180}


def live_session(race, now=None):
    """Label of the weekend's session running now ('FP1'), or None."""
    now = now or datetime.now(timezone.utc)
    return next((s for s, t in race['sessions'] if t <= now < t + timedelta(minutes=SESSION_MINUTES.get(s, 60))), None)


def next_race(now=None):
    """The current or upcoming weekend: race stays 'next' until 3 h after its start."""
    now = now or datetime.now(timezone.utc)
    return next((r for r in schedule() if r['sessions'][-1][1] + timedelta(hours=3) > now), None)


def round_counted(rnd, year='current'):
    """{'Race': bool, 'Sprint': bool}: whether Jolpica has results for that session of the round, i.e. whether its
    points are already in the standings. (The standings' own 'round' moves on after the sprint alone.)"""
    return {kind: bool(fetch(f'{JOLPICA}/{year}/{rnd}/{ep}.json?limit=1', 300)['MRData']['RaceTable']['Races'])
            for kind, ep in (('Race', 'results'), ('Sprint', 'sprint'))}


def circuit(key, year):
    """Track outline (x/y in the same 1/10 m frame as Position.z, starting at the line), corners, pit loss, rotation."""
    return fetch(f'https://api.multiviewer.app/api/v1/circuits/{key}/{year}')


def circuit_catalog():
    """{circuit key: {'name', 'years', 'pitLoss': {'normal', 'sc', 'vsc'}, ...}} for every circuit MultiViewer knows."""
    return fetch('https://api.multiviewer.app/api/v1/circuits', 7 * 86400)


def past_sessions(year=None, n=15):
    """Most recent finished sessions in the live timing archive: [(label, path)], newest first."""
    year = year or datetime.now(timezone.utc).year
    out = []
    for m in fetch(f'{ARCHIVE}{year}/Index.json', 600)['Meetings']:
        for s in m['Sessions']:
            if not (s.get('Path') and s.get('EndDate')):  # cancelled/unpublished sessions (older years) lack them
                continue
            h, mi, _ = s['GmtOffset'].lstrip('-').split(':')
            off = timedelta(hours=int(h), minutes=int(mi)) * (-1 if s['GmtOffset'].startswith('-') else 1)
            end = datetime.fromisoformat(s['EndDate']).replace(tzinfo=timezone.utc) - off
            if end < datetime.now(timezone.utc) - timedelta(minutes=30):
                out.append((f"{year} {m['Name']} - {s['Name']}", s['Path']))
    return out[::-1][:n]


def circuit_sessions(year, circuit_key):
    """Archive paths of that season's sessions at one circuit (Index.json meeting Circuit.Key), in order."""
    return [s['Path'] for m in fetch(f'{ARCHIVE}{year}/Index.json', 600)['Meetings']
            if (m.get('Circuit') or {}).get('Key') == circuit_key for s in m['Sessions'] if s.get('Path')]


FIRST_ARCHIVE_YEAR = 2018  # nothing earlier on F1's archive or FastF1's mirror


def replay_meetings(year):
    """[(meeting, [(session, path)])] in calendar order: finished sessions from the archive index, plus the ones it
    leaves out (2018 Australia, 2024 before Spain, 2023 Qatar after FP1), whose paths FastF1's schedule gives,
    kept only if the archive (or FastF1's mirror, which has the 2022 that F1 refuses) has their timing."""
    try:
        index = past_sessions(year, 999)[::-1]
    except requests.HTTPError:
        index = []
    meetings = {}  # meeting folder -> (name, sessions); the folder, because index and FastF1 names could differ
    for label, path in index:
        name, session = label[5:].rsplit(' - ', 1)
        meetings.setdefault(path.split('/')[1], (name, []))[1].append((session, path))
    known = {p for _, ss in meetings.values() for _, p in ss}

    import fastf1
    from concurrent.futures import ThreadPoolExecutor
    extra, now = [], datetime.now(timezone.utc)
    for _, ev in fastf1.get_event_schedule(year).iterrows():
        for i in range(1, 6):
            name, start = ev[f'Session{i}'], ev[f'Session{i}DateUtc']
            if name and name != 'None' and start.tz_localize('UTC') + timedelta(hours=4) < now:
                path = ev.get_session(name).api_path.removeprefix('/static/')
                if path not in known:
                    extra.append((ev['EventName'], name, path))

    def exists(path):
        return any(requests.head(f'{base}{path}TimingData.jsonStream', headers=UA, timeout=30).status_code == 200
                   for base in (ARCHIVE, MIRROR))
    with ThreadPoolExecutor(16) as pool:
        for (meeting, session, path), ok in zip(extra, pool.map(exists, [e[2] for e in extra])):
            if ok:
                meetings.setdefault(path.split('/')[1], (meeting, []))[1].append((session, path))
    return [meetings[k] for k in sorted(meetings)]  # folders start with the race date


def archive_stream(path, topic):
    """[(seconds since stream start, payload)] for one topic of an archived session ([] if the topic is missing)."""
    try:
        text = fetch(f'{ARCHIVE}{path}{topic}.jsonStream', raw=True)
    except requests.HTTPError:
        return []
    out = []
    for line in text.splitlines():
        if len(line) > 12:  # 'HH:MM:SS.mmm{json}'
            h, m, s = line[:12].split(':')
            out.append((int(h) * 3600 + int(m) * 60 + float(s), json.loads(line[12:])))
    return out
