import time

from f1tracker import live


def test_delay_holds_live_data_until_it_is_old_enough():
    st = live.State()
    live.LiveFeed(st)  # never started: State only checks the feed type
    st.delay = 30
    st.q.put(('LapCount', {'CurrentLap': 5}, time.time() - 31))
    st.q.put(('LapCount', {'CurrentLap': 6}, time.time() - 5))
    st.pump()
    assert st.session()['lap'] == 5  # the newer message waits
    st.delay = 0
    st.pump()
    assert st.session()['lap'] == 6


def test_track_message_is_spaced():
    st = live.State()
    for msg, shown in (('SCDeployed', 'SC Deployed'), ('VSCEnding', 'VSC Ending'), ('AllClear', 'All Clear'), ('Red', 'Red')):
        st.apply('TrackStatus', {'Status': '4', 'Message': msg}, 0)
        assert st.session()['track'] == shown


def test_safety_car_only_shown_while_it_moves():
    st = live.State()
    pos = lambda t, sc: {'Position': [{'Entries': {'1': {'X': t, 'Y': 0}, '241': {'X': sc, 'Y': 1}}}]}
    st.apply('Position', pos(0, 5), 0)
    st.apply('Position', pos(1, 5), 1)
    assert [c[0] for c in st.positions()[0]] == ['1']  # parked in the pit lane: hidden
    st.apply('Position', pos(2, 9), 2)
    assert {c[0] for c in st.positions()[0]} == {'1', '241'}  # deployed


def test_stopped_car_leaves_the_map_and_comes_back_if_it_restarts():
    st = live.State()
    st.apply('Position', {'Position': [{'Entries': {'77': {'X': 1, 'Y': 1}, '23': {'X': 2, 'Y': 2}}}]}, 0)
    st.apply('TimingData', {'Lines': {'77': {'Stopped': True}, '23': {'Stopped': False}}}, 0)
    assert [c[0] for c in st.positions()[0]] == ['23']
    st.apply('TimingData', {'Lines': {'77': {'Stopped': False}}}, 1)  # stall in practice: going again
    assert {c[0] for c in st.positions()[0]} == {'77', '23'}


def test_estimated_map_skips_stopped_cars():
    """No GPS (no F1 TV): positions come from timing progress along the outline."""
    st = live.State()
    st.track = live.Track({'x': [0, 100, 100, 0], 'y': [0, 0, 100, 100]})
    st.apply('TimingData', {'Lines': {'1': {'NumberOfLaps': 1}, '2': {'NumberOfLaps': 1}}}, 0)
    st.apply('TimingData', {'Lines': {'2': {'Stopped': True}}}, 1)
    cars, gps = st.positions()
    assert not gps and [c[0] for c in cars] == ['1']


def test_session_clock_counts_down_only_while_extrapolating():
    st = live.State()
    st.apply('ExtrapolatedClock', {'Remaining': '00:18:00', 'Extrapolating': False}, 0)
    st.t = 100  # no feed: State.now() is the last message time
    assert st.clock() == 18 * 60  # stopped (before the start, red flag)
    st.apply('ExtrapolatedClock', {'Remaining': '00:17:59', 'Extrapolating': True}, 100)
    st.t = 160
    assert st.clock() == 17 * 60 - 1


def test_quali_part_and_drop_zone():
    st = live.State()
    assert st.quali() is None  # practice / race
    st.apply('SessionInfo', {'Name': 'Sprint Qualifying', 'Type': 'Qualifying'}, 0)
    st.apply('TimingData', {'NoEntries': [0, 0, 0], 'SessionPart': 0}, 0)
    st.apply('TimingData', {'NoEntries': {'0': 22, '1': 16, '2': 10}, 'SessionPart': 1}, 1)
    assert st.quali() == {'part': 1, 'label': 'SQ1', 'safe': 16}
    st.apply('TimingData', {'SessionPart': 3}, 2)
    assert st.quali()['safe'] is None  # last part: nobody is knocked out


def test_tyre_colours_follow_the_season():
    """Pirelli 2018: seven dry compounds, soft yellow / medium white / hard ice blue. From 2019: soft red, medium yellow, hard white."""
    from f1tracker.widgets import tyre_style
    st = live.State()
    st.apply('SessionInfo', {'StartDate': '2018-03-25T16:10:00'}, 0)
    assert [tyre_style(c, st)[0] for c in ('HYPERSOFT', 'ULTRASOFT', 'SUPERSOFT', 'SUPERHARD')] == ['HS', 'US', 'SS', 'SH']
    assert tyre_style('SOFT', st)[1] == '#ffd400' and tyre_style('MEDIUM', st)[1] == '#f0f0f0'
    st.apply('SessionInfo', {'StartDate': '2019-03-17T16:10:00'}, 0)
    assert tyre_style('SOFT', st)[1] == '#ff3333' and tyre_style('MEDIUM', st)[1] == '#ffd400'
    assert tyre_style('ULTRASOFT', st)[0] == '?'
