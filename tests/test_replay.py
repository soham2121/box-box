import time

import pytest

from lightsout import data, live

GREEN, LAP_S, LAPS = 100.0, 90.0, 10


def fake_session():
    """Two cars, 10 laps of 90 s, green at t=100 (feed seconds). Car 1 leads, car 2 is 1 s behind."""
    msgs = [(0.0, 'SessionStatus', {'Status': 'Inactive'}), (0.5, 'LapCount', {'CurrentLap': 1, 'TotalLaps': LAPS}),
            (GREEN, 'SessionStatus', {'Status': 'Started'})]
    for n in range(1, LAPS + 1):
        t = GREEN + n * LAP_S
        if n < LAPS:
            msgs.append((t, 'LapCount', {'CurrentLap': n + 1}))
        for car, extra in (('1', 0.0), ('2', 1.0)):
            msgs.append((t + extra, 'TimingData', {'Lines': {car: {
                'NumberOfLaps': n, 'Position': car, 'LastLapTime': {'Value': '1:30.000'}}}}))
    return sorted(msgs, key=lambda m: m[0])


def start(speed=1.0):
    st = live.State()
    feed = live.ReplayFeed(st, 'fake/', speed, gps=False)
    feed.messages = fake_session
    feed.start()
    deadline = time.monotonic() + 5
    while not feed.end and time.monotonic() < deadline:
        time.sleep(0.01)
    return st, feed


def settle(st, feed, cond, limit=5):
    deadline = time.monotonic() + limit
    while time.monotonic() < deadline:
        st.pump()
        if cond():
            return True
        time.sleep(0.02)
    return False


def test_replay_starts_just_before_green_and_plays():
    st, feed = start(speed=1.0)
    try:
        assert feed.begin == GREEN - 30 and feed.end == GREEN + LAPS * LAP_S + 1
        assert feed.lap_starts[2] == GREEN + LAP_S
        assert settle(st, feed, lambda: st.d.get('SessionStatus', {}).get('Status') == 'Inactive')
        assert st.laps == []  # 30 s before green: nothing completed yet, nothing skipped ahead
    finally:
        feed.stop = True


def test_jump_forward_to_a_lap():
    st, feed = start(speed=1.0)
    try:
        feed.seek_lap(6)
        assert settle(st, feed, lambda: st.session()['lap'] == 6)
        # laps 1-5 logged on the way (the sim sees the history), except car 2's lap 5: it ends 1 s after the leader's
        assert len(st.laps) == 9
        assert abs(feed.now() - (feed.lap_starts[6] + 0.5)) < 1
    finally:
        feed.stop = True


def test_jump_backwards_rebuilds_the_session():
    st, feed = start(speed=1.0)
    try:
        feed.seek_lap(8)
        assert settle(st, feed, lambda: st.session()['lap'] == 8)
        feed.seek_lap(3)
        assert settle(st, feed, lambda: st.session()['lap'] == 3 and len(st.laps) == 4)
        assert [l['lap'] for l in st.laps if l['drv'] == '1'] == [1, 2]  # no laps from the future left behind
    finally:
        feed.stop = True


def test_pause_and_finish():
    st, feed = start(speed=1.0)
    try:
        feed.seek_lap(4)
        settle(st, feed, lambda: st.session()['lap'] == 4)
        feed.set_speed(0)
        t = feed.now()
        time.sleep(0.3)
        assert feed.now() == t and feed.status == 'replay paused'
        feed.seek(10 ** 6)  # past the end: clamps, plays everything, then reports finished
        assert settle(st, feed, lambda: feed.status == 'replay finished' and len(st.laps) == 2 * LAPS)
        feed.seek_lap(2)  # a finished replay can still jump back
        assert settle(st, feed, lambda: st.session()['lap'] == 2)
    finally:
        feed.stop = True


def test_merge_never_edits_the_message_it_came_from():
    first = {'Lines': {'1': {'NumberOfLaps': 1}}}
    state = live.merge(None, first)
    live.merge(state, {'Lines': {'1': {'NumberOfLaps': 2}}})
    assert first == {'Lines': {'1': {'NumberOfLaps': 1}}} and state['Lines']['1']['NumberOfLaps'] == 2


@pytest.mark.integration
def test_replay_years_fill_index_gaps():
    """The 2024 index starts at Spain and 2018's lacks Australia; FastF1's schedule fills both.
    F1 refuses most of 2022 (403); FastF1's mirror has it."""
    y24, y18, y22 = data.replay_meetings(2024), data.replay_meetings(2018), data.replay_meetings(2022)
    assert len(y24) == 25 and y24[1][0] == 'Bahrain Grand Prix'  # testing, then round 1
    assert y18[0][0] == 'Australian Grand Prix' and len(y18[0][1]) == 5
    assert sum(len(ss) for _, ss in y22) > 100 and y22[-1][0] == 'Abu Dhabi Grand Prix'
    paths = [p for _, ss in y24 for _, p in ss]
    assert len(paths) == len(set(paths))
