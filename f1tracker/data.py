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

APP_DIR = Path(os.getenv('APPDATA', Path.home())) / 'f1tracker'
CACHE = APP_DIR / 'cache'
UA = {'User-Agent': 'f1tracker/0.1'}  # MultiViewer rejects the default python-requests agent
JOLPICA = 'https://api.jolpi.ca/ergast/f1'
ARCHIVE = 'https://livetiming.formula1.com/static/'

TEAM_COLOURS = {  # Jolpica constructorId -> live timing TeamColour
    'mercedes': '00D7B6', 'ferrari': 'ED1131', 'mclaren': 'F47600', 'red_bull': '4781D7', 'rb': '6C98FF',
    'alpine': '00A1E8', 'haas': '9C9FA2', 'audi': 'F50537', 'sauber': '01C00E', 'williams': '1868DB',
    'aston_martin': '229971', 'cadillac': '909090',
}


def fetch(url, max_age=None, raw=False):
    """GET through a disk cache. max_age=None caches forever (history never changes). Offline -> stale cache."""
    f = CACHE / hashlib.md5(url.encode()).hexdigest()
    if f.exists() and (max_age is None or time.time() - f.stat().st_mtime < max_age):
        b = f.read_bytes()
    else:
        try:
            r = requests.get(url, headers=UA, timeout=30)
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


def _standings(kind):
    lists = fetch(f'{JOLPICA}/current/{kind}.json?limit=100', 1800)['MRData']['StandingsTable']['StandingsLists']
    return lists[0][kind[0].upper() + kind[1:]] if lists else []


def driver_standings():
    return [{'pos': i, 'code': s['Driver'].get('code') or s['Driver']['familyName'][:3].upper(),
             'name': f"{s['Driver']['givenName']} {s['Driver']['familyName']}",
             'team': s['Constructors'][-1]['name'], 'colour': TEAM_COLOURS.get(s['Constructors'][-1]['constructorId'], '888888'),
             'points': float(s['points']), 'wins': int(s['wins'])}
            for i, s in enumerate(_standings('driverStandings'), 1)]


def constructor_standings():
    return [{'pos': i, 'name': s['Constructor']['name'], 'colour': TEAM_COLOURS.get(s['Constructor']['constructorId'], '888888'),
             'points': float(s['points']), 'wins': int(s['wins'])}
            for i, s in enumerate(_standings('constructorStandings'), 1)]


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


def next_race(now=None):
    """The current or upcoming weekend: race stays 'next' until 3 h after its start."""
    now = now or datetime.now(timezone.utc)
    return next((r for r in schedule() if r['sessions'][-1][1] + timedelta(hours=3) > now), None)


def circuit(key, year):
    """Track outline (x/y in the same 1/10 m frame as Position.z, starting at the line), corners, pit loss, rotation."""
    return fetch(f'https://api.multiviewer.app/api/v1/circuits/{key}/{year}')


def past_sessions(year=None, n=15):
    """Most recent finished sessions in the live timing archive: [(label, path)], newest first."""
    year = year or datetime.now(timezone.utc).year
    out = []
    for m in fetch(f'{ARCHIVE}{year}/Index.json', 600)['Meetings']:
        for s in m['Sessions']:
            h, mi, _ = s['GmtOffset'].lstrip('-').split(':')
            off = timedelta(hours=int(h), minutes=int(mi)) * (-1 if s['GmtOffset'].startswith('-') else 1)
            end = datetime.fromisoformat(s['EndDate']).replace(tzinfo=timezone.utc) - off
            if s.get('Path') and end < datetime.now(timezone.utc) - timedelta(minutes=30):
                out.append((f"{year} {m['Name']} - {s['Name']}", s['Path']))
    return out[::-1][:n]


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
