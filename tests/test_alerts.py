from boxbox import alerts

LOSS = {'normal': 22.0, 'sc': 12.0, 'vsc': 15.0}


def car(code, drv, pos, gap, pit=False, out=False, stops=0):
    return {'code': code, 'drv': drv, 'pos': pos, 'gap': gap, 'int': '', 'pit': pit, 'out': out, 'stops': stops,
            'cmp': 'MEDIUM', 'age': 10, 'used': {'MEDIUM'}}


def snap(cars, status='1', rc=(), lap=20, laps=0):
    return {'ds': {c['code']: c for c in cars}, 'status': status, 'rc': len(rc), 'rc_msgs': list(rc), 'lap': lap, 'laps': laps}


def kinds(evs):
    return sorted(k for _, k, _, _ in evs)


FIELD = [car('VER', '1', 1, ''), car('LEC', '16', 2, '+1.5'), car('HAM', '44', 3, '+2.3'), car('NOR', '4', 4, '+3.0')]


def test_car_behind_pitting_is_an_undercut_threat_and_ahead_an_overcut_chance():
    now = [dict(c) for c in FIELD]
    now[3]['pit'] = True  # NOR, behind HAM
    evs = alerts.events(snap(FIELD), snap(now), [], ['HAM'], set(), LOSS)
    assert kinds(evs) == ['rival_pit'] and 'undercut' in evs[0][3] and '0.7s behind' in evs[0][3]
    now[1]['pit'] = True  # LEC, ahead
    assert {e[0][2] for e in alerts.events(snap(FIELD), snap(now), [], ['HAM'], set(), LOSS)} == {'NOR', 'LEC'}
    assert alerts.events(snap(FIELD), snap(now), [], [], set(), LOSS) == []  # nobody followed with alerts


def test_safety_car_says_what_a_stop_costs_now():
    evs = alerts.events(snap(FIELD), snap(FIELD, status='4'), [], ['HAM'], set(), LOSS)
    assert kinds(evs) == ['neutral'] and '~12s now (vs 22s)' in evs[0][3] and 'HAM P3 would rejoin P' in evs[0][3]
    assert alerts.events(snap(FIELD, status='4'), snap(FIELD, status='4'), [], ['HAM'], set(), LOSS) == []  # once


def test_stewards_and_retirements_for_my_cars_only():
    msg = {'Message': 'FIA STEWARDS: 5 SECOND TIME PENALTY FOR CAR 44 (HAM) - CAUSING A COLLISION'}
    other = {'Message': 'CAR 4 (NOR) TIME 1:32.1 DELETED - TRACK LIMITS'}
    evs = alerts.events(snap(FIELD), snap(FIELD, rc=[other, msg]), [], [], {'HAM', 'LEC'}, LOSS)
    assert kinds(evs) == ['steward'] and 'penalty' in evs[0][3].lower()
    gone = [dict(c) for c in FIELD]
    gone[1]['out'] = True
    assert [e[2] for e in alerts.events(snap(FIELD), snap(gone), [], [], {'HAM', 'LEC'}, LOSS)] == ['LEC out of the race']


def test_two_places_in_a_lap():
    laps = [{'drv': '44', 'lap': 19, 'pos': 5, 'pit': False}, {'drv': '44', 'lap': 20, 'pos': 3, 'pit': False}]
    evs = alerts.events(snap(FIELD, laps=1), snap(FIELD, laps=2), laps, ['HAM'], set(), LOSS)
    assert [e[2] for e in evs] == ['HAM gained 2 places']


def test_team_double_stack_swap_and_fight():
    team = {'LEC', 'HAM'}
    both = [dict(c) for c in FIELD]
    both[1]['pit'] = both[2]['pit'] = True
    assert kinds(alerts.events(snap(FIELD), snap(both), [], [], team, LOSS)) == ['double_stack']
    apart = [car('VER', '1', 1, ''), car('LEC', '16', 2, '+1.5'), car('HAM', '44', 3, '+4.0'), car('NOR', '4', 4, '+5.0')]
    closing = [apart[0], apart[1], car('HAM', '44', 3, '+2.2'), apart[3]]
    assert [e[2] for e in alerts.events(snap(apart), snap(closing), [], [], team, LOSS)] == ['Teammates fighting']
    swapped = [apart[0], car('HAM', '44', 2, '+1.5'), car('LEC', '16', 3, '+1.9'), apart[3]]
    assert [e[2] for e in alerts.events(snap(closing), snap(swapped), [], [], team, LOSS)] == ['Teammates swapped']


def test_slow_team_stop_from_its_stationary_time():
    stops = [{'drv': '16', 'lap': 18, 'stationary': 6.1}, {'drv': '44', 'lap': 18, 'stationary': 2.4}]
    laps = [{'drv': d, 'lap': 19, 'pos': p, 'pit': True, 't': 110.0} for d, p in (('16', 2), ('44', 3))]
    evs = alerts.events(snap(FIELD), snap(FIELD, laps=2), laps, [], {'LEC', 'HAM'}, LOSS, stops)
    assert [e[2] for e in evs] == ['LEC: slow pit stop']
