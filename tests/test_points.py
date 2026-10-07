from types import SimpleNamespace

import pytest

from f1tracker import data, live
from f1tracker.widgets import projection, session_kind

DRIVERS = [  # Jolpica standings rows (points before this session)
    {'pos': 1, 'code': 'ANT', 'team': 'Mercedes', 'colour': '00D7B6', 'points': 100.0, 'wins': 3},
    {'pos': 2, 'code': 'RUS', 'team': 'Mercedes', 'colour': '00D7B6', 'points': 90.0, 'wins': 1},
    {'pos': 3, 'code': 'VER', 'team': 'Red Bull', 'colour': '4781D7', 'points': 80.0, 'wins': 2},
]
TEAMS = [{'pos': 1, 'name': 'Mercedes', 'colour': '00D7B6', 'points': 190.0, 'wins': 4},
         {'pos': 2, 'name': 'Red Bull', 'colour': '4781D7', 'points': 80.0, 'wins': 2}]
DRIVER_LIST = {'12': {'Tla': 'ANT', 'TeamName': 'Mercedes', 'TeamColour': '00D7B6'},
               '63': {'Tla': 'RUS', 'TeamName': 'Mercedes', 'TeamColour': '00D7B6'},
               '1': {'Tla': 'VER', 'TeamName': 'Red Bull Racing', 'TeamColour': '4781D7'}}


def live_app(name='Race', type_='Race', counted=None, order=('VER', 'ANT', 'RUS'), cp=None):
    st = live.State()
    live.LiveFeed(st)  # registers itself as st.feed; the thread is never started
    st.apply('SessionInfo', {'Type': type_, 'Name': name}, 0)
    st.apply('SessionStatus', {'Status': 'Started'}, 0)
    st.apply('DriverList', DRIVER_LIST, 0)
    num = {v['Tla']: k for k, v in DRIVER_LIST.items()}
    st.apply('TimingData', {'Lines': {num[c]: {'Position': str(i)} for i, c in enumerate(order, 1)}}, 0)
    if cp:
        st.apply('ChampionshipPrediction', cp, 0)
    return SimpleNamespace(state=st, static={'drivers': DRIVERS, 'teams': TEAMS, 'counted': counted})


def test_session_kind():
    assert session_kind({'Type': 'Race', 'Name': 'Race'}) == 'Race'
    assert session_kind({'Type': 'Race', 'Name': 'Sprint'}) == 'Sprint'
    assert session_kind({'Type': 'Qualifying', 'Name': 'Sprint Qualifying'}) is None  # old code projected sprint points here
    assert session_kind({'Type': 'Practice', 'Name': 'Practice 1'}) is None


def test_feed_prediction_is_used_and_counts_once():
    cp = {'Drivers': {'12': {'CurrentPoints': 108.0, 'PredictedPoints': 126.0},  # 108 already includes the sprint
                      '63': {'CurrentPoints': 97.0, 'PredictedPoints': 112.0},
                      '1': {'CurrentPoints': 88.0, 'PredictedPoints': 113.0}},
          'Teams': {'Mercedes': {'TeamName': 'Mercedes', 'CurrentPoints': 205.0, 'PredictedPoints': 238.0},
                    'Red Bull Racing Ford': {'TeamName': 'Red Bull Racing', 'CurrentPoints': 88.0, 'PredictedPoints': 113.0}}}
    p = projection(live_app(cp=cp, counted={'Race': False, 'Sprint': True}))
    assert [(d['code'], d['points'], d['delta']) for d in p['drivers']] == [('ANT', 126.0, 18.0), ('VER', 113.0, 25.0), ('RUS', 112.0, 15.0)]
    assert [(t['name'], t['points'], t['delta'], t['colour']) for t in p['teams']] == [
        ('Mercedes', 238.0, 33.0, '00D7B6'), ('Red Bull Racing', 113.0, 25.0, '4781D7')]


def test_feed_updates_only_send_predicted_points():
    app = live_app(cp={'Drivers': {'1': {'CurrentPoints': 80.0, 'PredictedPoints': 80.0}}})
    app.state.apply('ChampionshipPrediction', {'Drivers': {'1': {'PredictedPoints': 105.0}}}, 1)
    assert app.state.prediction()['drivers'][0]['delta'] == 25.0


def test_fallback_race_after_sprint_weekend_still_projects():
    # Regression: Jolpica already has the sprint (standings 'round' == this round), race not yet -> project the race
    p = projection(live_app(counted={'Race': False, 'Sprint': True}))
    assert {d['code']: (d['points'], d['delta']) for d in p['drivers']} == {
        'ANT': (118.0, 18), 'RUS': (105.0, 15), 'VER': (105.0, 25)}
    assert p['drivers'][0]['code'] == 'ANT'
    assert {t['name']: t['delta'] for t in p['teams']} == {'Mercedes': 33, 'Red Bull': 25}


def test_fallback_sprint_uses_sprint_table():
    p = projection(live_app(name='Sprint', counted={'Race': False, 'Sprint': False}))
    assert {d['code']: d['delta'] for d in p['drivers']} == {'VER': 8, 'ANT': 7, 'RUS': 6}


def test_fallback_never_adds_points_jolpica_already_has():
    assert projection(live_app(counted={'Race': True, 'Sprint': True})) is None
    assert projection(live_app(name='Sprint', counted={'Race': False, 'Sprint': True})) is None
    assert projection(live_app(counted=RuntimeError('offline'))) is None  # unknown -> don't risk double counting


def test_no_projection_in_qualifying_or_without_live_feed():
    assert projection(live_app(name='Sprint Qualifying', type_='Qualifying', counted={'Race': False, 'Sprint': False})) is None
    app = live_app(counted={'Race': False, 'Sprint': False})
    app.state.feed = None
    assert projection(app) is None


def test_round_counted(monkeypatch):
    def fake(url, max_age=None, raw=False):
        races = [{'round': '12'}] if url.endswith('/sprint.json?limit=1') else []
        return {'MRData': {'RaceTable': {'Races': races}}}
    monkeypatch.setattr(data, 'fetch', fake)
    assert data.round_counted(12) == {'Race': False, 'Sprint': True}


@pytest.mark.integration
def test_jolpica_round_counted_live():
    assert data.round_counted(12, 2026) == {'Race': True, 'Sprint': True}  # Dutch GP 2026 was a sprint weekend


def replay_app(year, name='Race', best=None):
    """A replay of a `year` session, standings before it already loaded (widgets.BASES)."""
    from f1tracker import widgets
    st = live.State()
    live.ReplayFeed(st, 'x/')  # registers itself as st.feed; never started
    path = f'{year}/{year}-07-18_British_Grand_Prix/{year}-07-18_Race/'
    st.apply('SessionInfo', {'Type': 'Race', 'Name': name, 'Path': path, 'StartDate': f'{year}-07-18T14:00:00'}, 0)
    st.apply('SessionStatus', {'Status': 'Started'}, 0)
    st.apply('DriverList', DRIVER_LIST, 0)
    lines = {'1': {'Position': '1'}, '12': {'Position': '2'}, '63': {'Position': '11'}}
    for n, t in (best or {}).items():
        lines[n]['BestLapTime'] = {'Value': t}
    st.apply('TimingData', {'Lines': lines}, 0)
    widgets.BASES[path] = {'drivers': [dict(d) for d in DRIVERS], 'teams': [dict(t) for t in TEAMS], 'round': 10}
    return SimpleNamespace(state=st, static={})


def test_replay_projection_uses_that_seasons_rules():
    # 2019-2024: +1 for the fastest lap if it's in the top 10
    p = projection(replay_app(2021, best={'1': '1:30.000', '12': '1:31.000'}))
    assert {d['code']: d['delta'] for d in p['drivers']} == {'VER': 26, 'ANT': 18, 'RUS': 0}
    p = projection(replay_app(2021, best={'63': '1:29.000', '1': '1:30.000'}))  # fastest lap by P11: no point
    assert {d['code']: d['delta'] for d in p['drivers']} == {'VER': 25, 'ANT': 18, 'RUS': 0}
    p = projection(replay_app(2025, best={'1': '1:30.000'}))  # dropped from 2025
    assert {d['code']: d['delta'] for d in p['drivers']}['VER'] == 25
    # 2021's sprint ("Sprint Qualifying", Type Race) paid 3-2-1
    p = projection(replay_app(2021, name='Sprint Qualifying'))
    assert {d['code']: d['delta'] for d in p['drivers']} == {'VER': 3, 'ANT': 2, 'RUS': 0}
