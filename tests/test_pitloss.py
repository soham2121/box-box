import pytest

from conftest import make_race
from f1tracker import live, sim

STOPS = [('2', 12), ('4', 14), ('6', 16), ('8', 18)]


def stops(pairs, stationary=2.5):
    return [{'drv': d, 'lap': n, 'stationary': stationary} for d, n in pairs]


def test_normal_stops_give_the_real_loss():
    laps = make_race(stops=STOPS)
    r = sim.pit_loss(laps, stops(STOPS))
    assert r['source'] == 'this session' and r['n'] == 4
    assert r['normal'] == pytest.approx(22.0, abs=0.3)


def test_lap_one_pit_flags_are_not_stops():
    laps = make_race(stops=[('3', 10)], lap1_pit=True)
    assert sim.stops_from_laps(laps) == [{'drv': '3', 'lap': 10}]


def test_flag_fallback_finds_stops_when_pitstopseries_is_missing():
    laps = make_race(stops=STOPS)
    r = sim.pit_loss(laps, None)  # None: derive from the lap flags
    assert r['source'] == 'this session' and r['normal'] == pytest.approx(22.0, abs=0.3)


def test_sc_and_vsc_laps_are_excluded():
    laps = make_race(stops=STOPS + [('1', 20), ('3', 20)], sc={20, 21}, loss=22.0)
    # the two stops under the (V)SC (the sc flag covers both) would read low; they must not be used
    assert len(sim.pit_loss_samples(laps, stops(STOPS + [('1', 20), ('3', 20)]))) == 4


def test_only_neutralised_stops_fall_back_to_a_labelled_source():
    pairs = [('1', 20), ('3', 20), ('5', 21)]
    laps = make_race(stops=pairs, sc={20, 21, 22})
    r = sim.pit_loss(laps, stops(pairs), typical=21.2)
    assert (r['source'], r['normal'], r['n']) == ('typical circuit', 21.2, 0)


def test_slow_opening_laps_without_sc_flag_are_ignored():
    # Bahrain 2026: race started behind the SC after a red flag, TrackStatus stayed green, laps 2-3 took 140-200 s
    pairs = [('1', 2), ('3', 2), ('5', 2), ('7', 3), ('9', 3), ('10', 3)] + STOPS  # junk outnumbers real stops
    laps = make_race(stops=pairs, slow={2: 2.0, 3: 1.5}, loss=60.0)
    for l in laps:  # the real stops later in the race lose 22 s
        if (l['drv'], l['lap'] - 1) in set(STOPS):
            l['t'] -= 38.0
    r = sim.pit_loss(laps, stops(pairs))
    assert r['n'] == 4 and r['normal'] == pytest.approx(22.0, abs=0.3)


def test_two_stops_are_not_enough():
    laps = make_race(stops=STOPS[:2])
    assert sim.pit_loss(laps, stops(STOPS[:2]), typical=21.2)['source'] == 'typical circuit'
    assert sim.pit_loss(laps, stops(STOPS[:2]))['source'] == 'default'
    wk = {'normal': 24.0, 'source': 'sprint', 'n': 5}
    assert sim.pit_loss(laps, stops(STOPS[:2]), weekend=wk, typical=21.2)['normal'] == 24.0


def test_slow_stationary_time_is_replaced_by_the_typical_one():
    laps = make_race(stops=STOPS)
    for l in laps:  # driver 2's stop had a 12 s wheel problem: 9.5 s more than the others' 2.5 s
        if (l['drv'], l['lap']) == ('2', 12):
            l['t'] += 9.5
    s = stops(STOPS)
    s[0]['stationary'] = 12.0
    assert sim.pit_loss(laps, s)['normal'] == pytest.approx(22.0, abs=0.3)


def test_circuit_api_value_wins_and_sc_vsc_follow():
    laps = make_race(stops=STOPS)
    r = sim.pit_loss(laps, stops(STOPS), known={'normal': 21.8, 'sc': 13.81, 'vsc': 15.77})
    assert (r['source'], r['normal'], r['sc'], r['vsc']) == ('circuit data', 21.8, 13.81, 15.77)
    r = sim.pit_loss(laps, stops(STOPS))
    assert r['sc'] == pytest.approx(r['normal'] * sim.MODEL['sc_pit_ratio'])
    assert r['vsc'] == pytest.approx(r['normal'] * sim.MODEL['vsc_pit_ratio'])


# ---- State: stops, flags -------------------------------------------------------------------------------------------

def test_state_reads_pitstopseries_in_both_shapes():
    st = live.State()
    st.apply('PitStopSeries', {'PitTimes': {'11': [{'PitStop': {'Lap': '9', 'PitLaneTime': '28.1', 'PitStopTime': '2.5'}}]}}, 0)
    st.apply('PitStopSeries', {'PitTimes': {'11': {'1': {'PitStop': {'Lap': '30', 'PitLaneTime': '27.9', 'PitStopTime': '3.1'}}}}}, 1)
    assert [(s['drv'], s['lap'], s['lane'], s['stationary']) for s in st.stops()] == [('11', 9, 28.1, 2.5), ('11', 30, 27.9, 3.1)]


def lap_msg(n, laps, t='1:30.000', **kw):
    return {'Lines': {n: {'NumberOfLaps': laps, 'LastLapTime': {'Value': t}, 'Position': '1', **kw}}}


def test_pit_flag_ignored_before_the_session_starts():
    st = live.State()
    st.apply('SessionStatus', {'Status': 'Inactive'}, 0)
    st.apply('TimingData', {'Lines': {'1': {'PitOut': True}}}, 1)  # pit lane -> grid
    st.apply('SessionStatus', {'Status': 'Started'}, 2)
    st.apply('TimingData', lap_msg('1', 1), 3)
    assert st.laps[-1]['pit'] is False


def test_vsc_marks_the_lap():
    st = live.State()
    st.apply('SessionStatus', {'Status': 'Started'}, 0)
    st.apply('TimingData', lap_msg('1', 1), 1)
    st.apply('TrackStatus', {'Status': '6', 'Message': 'VSCDeployed'}, 2)
    st.apply('TimingData', lap_msg('1', 2), 3)
    st.apply('TrackStatus', {'Status': '1', 'Message': 'AllClear'}, 4)
    st.apply('TimingData', lap_msg('1', 3), 5)
    st.apply('TimingData', lap_msg('1', 4), 6)
    assert [l['sc'] for l in st.laps] == [False, True, True, False]


# ---- real data -----------------------------------------------------------------------------------------------------

@pytest.mark.integration
def test_bahrain_2026_pit_loss():
    path = '2026/2026-10-04_Bahrain_Grand_Prix/2026-10-04_Race/'
    st = live.State()
    msgs = live.ReplayFeed(st, path, gps=False).messages()
    live.load_track(st, next(x for t, tp, x in msgs if tp == 'SessionInfo'))
    at11 = None
    for t, tp, x in msgs:
        st.apply(tp, x, t)
        if at11 is None and st.session()['lap'] >= 11:
            at11 = st.pit_loss()
    assert at11['source'] == 'typical circuit' and 20 < at11['normal'] < 25  # no green stop yet; no circuit data
    end = st.pit_loss()
    assert end['source'] == 'this session' and end['n'] >= 15 and 24 < end['normal'] < 29
