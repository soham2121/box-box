import numpy as np
import pytest

from conftest import make_race
from boxbox import calibrate, sim


def model(total=10, base=None, **knobs):
    """A fitted-model dict by hand: no SC/VSC, tiny noise, so outcomes are decided by the rules under test."""
    base = base or {'A': 90.0, 'B': 90.0}
    return {'m': {**sim.DEFAULTS, 'sc_per_race': 0.0, 'vsc_per_race': 0.0, **knobs}, 'total': total, 'base': base,
            'off': {c: o for c, (o, _) in sim.DEFAULTS['prior'].items()}, 'deg': {c: 0.0 for c in sim.DEFAULTS['prior']},
            'trend': 0.0, 'sigma': {d: 0.01 for d in base}}


def car(drv, pos, gap, cmp='MEDIUM', used=None, age=5, interval=''):
    return {'drv': drv, 'code': drv, 'pos': pos, 'out': False, 'cmp': cmp, 'age': age, 'gap': gap, 'int': interval,
            'used': used or {cmp}}


def winner(drivers, lap, m, **kw):
    r = sim.simulate(drivers, lap, m, runs=50, pit_loss={'normal': 22.0}, **kw)
    return r['drv'][int(np.argmin(r['pos'].mean(0)))]


def test_lapped_cars_keep_their_interval_in_seconds():
    rows = [car('1', 1, 'LAP 30'), car('2', 2, '+12.5', interval='+12.5'), car('3', 3, '1L', interval='+30.0'),
            car('4', 4, '2L', interval='1L')]
    assert sim.gaps(rows, lap_s=90.0) == {'1': 0.0, '2': 12.5, '3': 42.5, '4': 180.0}


def test_two_compound_rule_applies_to_grands_prix_not_sprints():
    # A leads by 10 s but has only run mediums: in a Grand Prix it must stop (22 s), in a sprint it needn't
    cars = [car('A', 1, ''), car('B', 2, '+10.0', 'HARD', {'MEDIUM', 'HARD'})]
    assert winner(cars, 7, model(two_compounds=True)) == 'B'
    assert winner(cars, 7, model(two_compounds=False)) == 'A'


def test_cars_on_the_wrong_tyres_for_the_track_lose_out():
    cars = [car('A', 1, '', 'MEDIUM', {'MEDIUM', 'HARD'}), car('B', 2, '+10.0', 'INTERMEDIATE')]
    assert sim.track_now(cars) == 'DRY'  # one each: ties count as dry
    assert winner(cars, 0, model(total=20), conditions='WET') == 'B'  # slicks in the rain: pit or lose 6 s a lap
    assert winner(cars, 0, model(total=20), conditions='DRY') == 'A'


def test_calibrated_passing_odds_decide_a_close_battle():
    cars = [car('A', 1, ''), car('B', 2, '+0.5', interval='+0.5')]
    m = lambda w: model(base={'A': 91.0, 'B': 90.0}, two_compounds=False, **{'pass': w})  # B a second a lap faster, no stops
    assert winner(cars, 5, m([30.0, 0.0, 0.0])) == 'B'   # always gets past
    assert winner(cars, 5, m([-30.0, 0.0, 0.0])) == 'A'  # never gets past: stuck behind


def test_pass_fit_recovers_the_odds():
    rng = np.random.default_rng(0)
    edge, gap = rng.normal(0, 1, 5000), rng.uniform(0, 1, 5000)
    p = 1 / (1 + np.exp(-(0.5 + 1.0 * edge - 4.0 * gap)))
    w = calibrate.fit_pass(list(zip(edge, gap, rng.random(5000) < p)))
    assert w == pytest.approx([0.5, 1.0, -4.0], abs=0.3)


def test_a_planned_stop_for_slicks_still_pits_again_when_it_rains():
    # A stops for mediums on lap 3, rain from lap 5: A must still go to inters, not run slicks to the flag
    cars = [car('A', 1, '', used={'MEDIUM', 'HARD'}), car('C', 2, '+40.0', used={'MEDIUM', 'HARD'})]
    r = sim.simulate(cars, 1, model(total=20, base={'A': 90.0, 'C': 90.0}), plan=('A', 3, 'MEDIUM'), runs=20,
                     pit_loss={'normal': 22.0}, conditions={5: 'WET'})
    assert (r['pos'][:, r['drv'].index('A')] == 1).all()  # 2 stops (44 s) beats C's 40 s gap + 1 stop


def test_each_season_gets_its_era_and_regulation_facts(monkeypatch):
    assert [sim.era(y) for y in (2018, 2021, 2022, 2025, 2026, 2030)] == \
        ['2018-2021', '2018-2021', '2022-2025', '2022-2025', '2026-', '2026-']
    monkeypatch.setattr(sim, 'CALIBRATION', {'2022-2025': {'sc_per_race': 0.9}, '2026-': {'sc_per_race': 0.6}})
    m = sim.season_model(2024)
    assert (m['min_mass_kg'], m['fuel_start_kg'], m['sc_per_race']) == (798, 100, 0.9)
    assert sim.season_model(2026)['sc_per_race'] == 0.6 and sim.season_model(2026)['min_mass_kg'] == 768
    assert sim.season_model(2019)['sc_per_race'] == sim.DEFAULTS['sc_per_race']  # no 2018-21 fit: never borrows another era's
    assert sim.season_model(2024, {'sc_per_race': 0.1})['sc_per_race'] == 0.1  # config overrides win


def test_2018_compounds_become_relative_names():
    laps = [{'cmp': c} for c in ('ULTRASOFT', 'SUPERSOFT', 'SOFT', 'INTERMEDIATE')]
    names = sim.relative_compounds(laps)
    assert names == {'ULTRASOFT': 'SOFT', 'SUPERSOFT': 'MEDIUM', 'SOFT': 'HARD'}
    assert sim.relative_compounds([{'cmp': 'SOFT'}, {'cmp': 'HARD'}]) == {}  # 2019+: already relative
    row = sim.relabel([{'cmp': 'SUPERSOFT', 'used': {'ULTRASOFT', 'SUPERSOFT'}}], names)[0]
    assert row == {'cmp': 'MEDIUM', 'used': {'SOFT', 'MEDIUM'}}


def test_safety_car_now_bunches_the_field_and_makes_stops_cheap():
    cars = [car('A', 1, '', used={'MEDIUM', 'HARD'}), car('B', 2, '+15.0', used={'MEDIUM', 'HARD'})]
    m = model(total=20, base={'A': 90.0, 'B': 89.9})  # B a tenth faster: can't make up 15 s, can make up 0.8 s
    pass_now = lambda **kw: sim.simulate(cars, 5, m, runs=40, pit_loss={'normal': 22.0, 'sc': 12.0}, **kw)
    a, b = pass_now(), pass_now(neutral_now=('SC', 3))
    j = a['drv'].index('B')
    assert (a['pos'][:, j] == 2).all()  # green: 15 s is too much
    assert sim.simulate(cars, 5, {**m, 'm': {**m['m'], 'pass': [-2.0, 40.0, 0]}}, runs=40, pit_loss={'normal': 22.0},
                        neutral_now=('SC', 3))['pos'][:, j].mean() < 1.5  # bunched up: B gets the restart


def test_forced_safety_car_makes_pitting_under_it_the_best_window():
    # A leads by 15 s and still owes its stop: 22 s under green loses the lead, 10 s as the SC comes out keeps it
    cars = [car('A', 1, ''), car('B', 2, '+15.0', 'HARD', {'MEDIUM', 'HARD'}, age=0)]  # B: fresh hards, no SC stop
    best = sim.best_window(cars, 2, model(total=30), 'A', runs=30, pit_loss={'normal': 22.0, 'sc': 10.0},
                           compounds=('HARD',), events={12: 'SC'})
    assert best[0][1] == 12 and best[0][0] == 1.0


def test_red_flag_gives_everyone_free_tyres():
    # A leads by 10 s but owes the two-compound stop: a red flag lets it change tyres for free and stay ahead
    cars = [car('A', 1, ''), car('B', 2, '+10.0', 'HARD', {'MEDIUM', 'HARD'})]
    assert winner(cars, 3, model(total=12)) == 'B'
    assert winner(cars, 3, model(total=12), events={5: 'RED'}) == 'A'


def test_pit_neutral_fit_finds_the_herd_effect():
    # 150 SCs of 20 cars: each SC has its own shared pull (sd 2) on top of a tyre-age effect
    rng = np.random.default_rng(1)
    eps = []
    for _ in range(150):
        age = rng.uniform(0, 1, 20)
        x = [[1.0, a, 0.5, 0.0, 1.0, 0.0, 0.0] for a in age]
        z = -2.0 + 2.0 * age + rng.normal(0, 2.0)
        eps.append({'kind': 'sc', 'x': x, 'y': list(rng.random(20) < 1 / (1 + np.exp(-z)))})
    w, sd = calibrate.fit_pit_neutral(eps)
    assert sd['sc'] == pytest.approx(2.0, abs=0.5) and w[1] == pytest.approx(2.0, abs=0.6)


def test_pace_uncertainty_widens_the_spread_of_finishes():
    """A 3 s lead over 10 laps: certain with an exact fit, doubtful once each run draws the true pace (pace_sd)."""
    cars = [car('A', 1, ''), car('B', 2, '+3.0', interval='+3.0')]
    pos = lambda sd: sim.simulate(cars, 0, model(two_compounds=False, **{'pass': [30.0, 0.0, 0.0], 'pace_sd': sd}),
                                  runs=400, pit_loss={'normal': 22.0})['pos'][:, 0]
    assert (pos(0.0) == 1).all()
    assert 0.2 < (pos(0.5) == 2).mean() < 0.8


def test_no_clean_laps_yet_takes_the_level_of_the_laps_seen():
    """A start behind the SC: no green laps, so lap times come from the SC laps scaled back, not a fixed 90 s."""
    laps = [{'drv': d, 'lap': n, 't': 150.0, 'pit': False, 'sc': True, 'cmp': 'INTERMEDIATE', 'age': n, 'int': 2.0,
             'pos': i + 1, 'gap': 2.0 * i} for n in range(2, 6) for i, d in enumerate('AB')]
    f = sim.fit(laps, 20, sim.DEFAULTS)
    assert abs(sim.predict(f, 'A', 6, 'INTERMEDIATE', 6) - 150.0 / sim.DEFAULTS['sc_lap_factor']) < 2


def test_stops_owed_counts_tyre_life_and_the_two_compound_rule():
    m = {**sim.MODEL, 'life': {'SOFT': 20, 'MEDIUM': 30, 'HARD': 40}, 'two_compounds': True}
    d = lambda cmp, age, used: {'cmp': cmp, 'age': age, 'used': set(used)}
    assert sim.stops_owed(d('HARD', 10, ['MEDIUM']), 20, m) == 0  # rule done, the set lasts
    assert sim.stops_owed(d('MEDIUM', 10, []), 15, m) == 1  # lasts, but still owes another compound
    assert sim.stops_owed(d('SOFT', 15, ['MEDIUM']), 50, m) == 2  # 5 laps left on it, then 45 on 40-lap hards
    assert sim.stops_owed(d('INTERMEDIATE', 3, []), 30, m) == 0  # wet: the weather decides
    assert sim.stops_owed(d('MEDIUM', 10, []), 15, {**m, 'two_compounds': False}) == 0  # sprint


def test_battle_gap_net_of_stops_pace_and_catch():
    laps = make_race(drivers=3, laps=20)  # driver i is 0.1 s/lap slower than driver i-1
    ds = [{'drv': '1', 'code': 'AAA', 'pos': 1, 'gap': '', 'int': '', 'cmp': 'HARD', 'age': 10, 'used': {'MEDIUM'}, 'out': False},
          {'drv': '2', 'code': 'BBB', 'pos': 2, 'gap': '+3.0', 'int': '+3.0', 'cmp': 'MEDIUM', 'age': 10, 'used': set(), 'out': False},
          {'drv': '3', 'code': 'CCC', 'pos': 3, 'gap': '+3.8', 'int': '+0.8', 'cmp': 'HARD', 'age': 5, 'used': {'MEDIUM'}, 'out': False}]
    b = sim.battle(ds, laps, '2', '1', 20, 30, 22.0)
    assert b['gap'] == pytest.approx(-3.0) and b['owed'] == (1, 0)
    assert b['net'] == pytest.approx(-3.0 - 22.0)  # BBB still has to stop: further behind once both have
    assert b['edge'] == pytest.approx(-0.1) and b['catch'] is None  # slower: never catches
    c = sim.battle(ds, laps, '2', '3', 20, 30, 22.0)
    assert c['gap'] == pytest.approx(0.8) and c['close'] and c['net'] == pytest.approx(0.8 - 22.0)  # CCC ahead net
    assert sim.battle(ds, laps, '3', '2', 20, 30, 22.0)['catch'] is None  # CCC is behind on track: nothing to catch
    assert len(b['trend']) == 10
    assert sim.real_rival(ds, laps, '2', 20, 30, 22.0)[0] == '3'


def test_traffic_laps_kept_when_a_driver_has_few_others():
    laps = make_race(drivers=4, laps=12)
    for l in laps:  # driver 4 sat within 1 s of the car ahead from lap 3 on
        if l['drv'] == '4' and l['lap'] >= 3:
            l['int'] = 0.6
        elif l['drv'] == '3':
            l['int'] = 0.6 if l['lap'] == 5 else 2.0  # one lap in traffic among plenty without
    kept = sim.clean(laps)
    assert sum(l['drv'] == '4' for l in kept) == 11  # laps 2-12, all of them: only lap 2 was free
    assert not any(l['drv'] == '3' and l['lap'] == 5 for l in kept)  # dropped: driver 3 has enough free laps
