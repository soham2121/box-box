import json
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


def test_learned_timing_points_per_sector():
    tr = live.Track({'x': [0, 100, 100, 0], 'y': [0, 0, 100, 100]})
    assert list(tr.bounds([2, 2])) == [0.25, 0.5, 0.75, 1.0]  # nothing learned: equal lengths
    tr.learned = {'sectors': [[0.1, 0.3], [0.6, 1.0]], 'profile': None}
    assert list(tr.bounds([2, 2])) == [0.1, 0.3, 0.6, 1.0]
    # another count in sector 2 (Baku 2026: 8/10/8 in qualifying, 8/10/9 in the race): spread between its learned ends
    assert [round(b, 3) for b in tr.bounds([2, 3])] == [0.1, 0.3, 0.533, 0.767, 1.0]
    assert tr.advance(0.2, 0.1) == 0.2 + 0.1  # constant speed without a profile
    tr.learned['profile'] = [0.0, 0.25, 1.0]  # first half of the lap time covers a quarter of the distance
    assert tr.advance(0.0, 0.5) == 0.25 and tr.advance(0.25, 0.25) == 0.625


def test_safety_car_out_now_for_the_sim():
    st = live.State()
    m = {'sc_laps': 4, 'vsc_laps': 2}
    st.apply('LapCount', {'CurrentLap': 10}, 0)
    assert st.neutral_now(m) is None
    st.apply('TrackStatus', {'Status': '4', 'Message': 'SCDeployed'}, 1)
    st.apply('LapCount', {'CurrentLap': 11}, 2)
    assert st.neutral_now(m) == ('SC', 3)  # typical 4 laps, 1 run
    st.apply('RaceControlMessages', {'Messages': {'226': {'Category': 'SafetyCar', 'Status': 'IN THIS LAP'}}}, 3)
    assert st.neutral_now(m) == ('SC', 1)
    st.apply('TrackStatus', {'Status': '1', 'Message': 'AllClear'}, 4)
    assert st.neutral_now(m) is None
    st.apply('TrackStatus', {'Status': '6', 'Message': 'VSCDeployed'}, 5)
    assert st.neutral_now(m) == ('VSC', 2)
    st.apply('TrackStatus', {'Status': '7', 'Message': 'VSCEnding'}, 6)
    assert st.neutral_now(m) is None


def test_cars_glide_between_fixes_only_with_enough_delay():
    """Live with a delay >= SMOOTH_DELAY the next GPS fix is already held, so the map draws between the two."""
    st = live.State()
    live.LiveFeed(st)
    now = time.time()
    pos = lambda x, ts: {'Position': [{'Timestamp': ts, 'Entries': {'1': {'X': x, 'Y': 1}}}]}
    st.q.put(('Position', pos(0, '2026-10-11T12:00:00.0Z'), now - 11))
    st.q.put(('Position', pos(100, '2026-10-11T12:00:02.0Z'), now - 9))  # still held at delay 10
    st.delay = 10
    st.pump()
    x = st.positions()[0][0][1]
    assert 40 < x < 60  # halfway between the fix 11 s ago and the one 9 s ago
    st.delay = live.SMOOTH_DELAY - 1  # too little: jumps to the latest fix it has applied
    st.pump()
    assert st.positions()[0][0][1] == 100


def test_samples_in_one_message_spread_by_their_timestamps():
    st = live.State()
    st.add_trail({'Position': [{'Timestamp': '2026-10-11T12:00:00.0Z', 'Entries': {'1': {'X': 0, 'Y': 1}}},
                               {'Timestamp': '2026-10-11T12:00:00.5Z', 'Entries': {'1': {'X': 10, 'Y': 1}}}]}, 100)
    assert st.glide('1', 99.75) == (5, 1)  # the last sample is at the message time, the first 0.5 s before it
    assert st.glide('1', 200) == (10, 1)  # nothing newer: holds the last fix


def test_late_message_keeps_its_place_by_gps_clock():
    """A message that arrives 1 s late still sits 1 s after the one before it, not 2 s: cars don't rubber-band."""
    st = live.State()
    pos = lambda x, ts: {'Position': [{'Timestamp': ts, 'Entries': {'1': {'X': x, 'Y': 1}}}]}
    st.add_trail(pos(0, '2026-10-11T12:00:00.0Z'), 100)
    st.add_trail(pos(10, '2026-10-11T12:00:01.0Z'), 102)  # late
    st.add_trail(pos(20, '2026-10-11T12:00:02.0Z'), 102)  # on time again
    assert [s[0] for s in st.trail['1']] == [100, 101, 102]


def test_yellow_sectors_follow_race_control():
    st = live.State()
    msg = lambda flag, scope, sector=None: {'Category': 'Flag', 'Flag': flag, 'Scope': scope, 'Sector': sector}
    st.apply('RaceControlMessages', {'Messages': [msg('YELLOW', 'Sector', 6), msg('DOUBLE YELLOW', 'Sector', 7)]}, 0)
    assert st.yellow_sectors() == {6: 'YELLOW', 7: 'DOUBLE YELLOW'}
    st.apply('RaceControlMessages', {'Messages': {'2': msg('CLEAR', 'Sector', 7)}}, 1)
    assert st.yellow_sectors() == {6: 'YELLOW'}
    st.apply('RaceControlMessages', {'Messages': {'3': msg('GREEN', 'Track')}}, 2)
    assert st.yellow_sectors() == {}


def test_marshal_sectors_snap_in_lap_order_and_the_last_wraps():
    sq = {'x': [0, 50, 100, 100, 100, 50, 0, 0], 'y': [0, 0, 0, 50, 100, 100, 100, 50]}  # square, 8 points
    pos = lambda x, y, n: {'number': n, 'trackPosition': {'x': x, 'y': y}}
    tr = live.Track({**sq, 'marshalSectors': [pos(100, 0, 2), pos(0, 0, 1), pos(50, 100, 3)],
                     'corners': [{'number': 1, 'angle': 90, 'trackPosition': {'x': 100, 'y': 0}}]})
    assert tr.marshal == {1: (0, 2), 2: (2, 5), 3: (5, 0)}  # sector 3 runs from point 5 back to the line
    assert tr.corners == [(1, 100, 0, 90)]


def test_same_tyres_through_the_pits_stay_one_stint():
    """Red flag: F1 opens a new stint with TyresNotChanged '1' for cars that keep their tyres."""
    from f1tracker.widgets import stints
    st = live.State()
    st.apply('TimingAppData', {'Lines': {'1': {'Stints': [
        {'Compound': 'INTERMEDIATE', 'New': 'true', 'TyresNotChanged': '0', 'TotalLaps': 2, 'StartLaps': 0},
        {'Compound': 'INTERMEDIATE', 'New': 'false', 'TyresNotChanged': '1', 'TotalLaps': 3, 'StartLaps': 2},
        {'Compound': 'SOFT', 'New': 'true', 'TyresNotChanged': '0', 'TotalLaps': 5, 'StartLaps': 0}]}}}, 0)
    assert [(s['Compound'], s['TotalLaps'], s['StartLaps']) for s in stints(st, '1')] == [('INTERMEDIATE', 3, 0), ('SOFT', 5, 0)]


def test_radio_transcript_cached_and_queued_once(monkeypatch):
    from f1tracker import radio
    monkeypatch.setattr(radio, '_done', {'u1': 'box box'})
    monkeypatch.setattr(radio, '_asked', set())
    monkeypatch.setattr(radio, '_worker', object())  # pretend it runs: nothing transcribes in a test
    jobs = radio.queue.LifoQueue()
    monkeypatch.setattr(radio, '_jobs', jobs)
    assert radio.text('u1') == 'box box'
    assert radio.text('u2') is None and radio.text('u2') is None
    assert jobs.qsize() == 1  # asked twice, queued once


def test_live_session_restored_after_reopening(tmp_path, monkeypatch):
    """The dashboard closed and opened mid-session: what it recorded goes in before the server's snapshot."""
    monkeypatch.setattr(live.data, 'APP_DIR', tmp_path)
    feed = live.LiveFeed(live.State(), record=True)
    feed.push('SessionInfo', {'Key': 11388})
    feed.push('LapCount', {'CurrentLap': 5})
    feed.push('Position.z', 'not recorded')
    feed.out.close()
    st = live.State()
    feed = live.LiveFeed(st, record=True)
    feed.push('SessionInfo', {'Key': 11388})
    got = [m[:2] for m in list(st.q.queue)]
    assert got == [('SessionInfo', {'Key': 11388}), ('LapCount', {'CurrentLap': 5}), ('SessionInfo', {'Key': 11388})]
    feed.out.close()


def test_recorder_without_state_only_writes(tmp_path, monkeypatch):
    """The tray records while the dashboard is closed: nothing queued, nothing restored, every message to the file."""
    monkeypatch.setattr(live.data, 'APP_DIR', tmp_path)
    feed = live.LiveFeed(None, record=True)
    feed.push('SessionInfo', {'Key': 7})
    feed.push('LapCount', {'CurrentLap': 2})
    feed.out.close()
    assert [json.loads(l)[1] for l in (tmp_path / 'sessions' / '7.jsonl').open()] == ['SessionInfo', 'LapCount']
