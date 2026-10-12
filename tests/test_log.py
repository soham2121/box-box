import logging

from lightsout import main


def test_tokens_never_reach_the_log():
    jwt = 'eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiIxMjMifQ.c2lnbmF0dXJl'
    rec = logging.LogRecord('x', logging.INFO, __file__, 1, 'subscription token %s ok', (jwt,), None)
    assert main.NoTokens().filter(rec) and rec.getMessage() == 'subscription token <token> ok'


def test_prints_become_log_lines_even_when_split_across_writes():
    got = []
    handler = logging.Handler()
    handler.emit = lambda r: got.append(r.getMessage())
    log = logging.getLogger('test-lines')
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    try:
        out = main.Lines('test-lines', None)  # no console: pythonw
        out.write('radio clip failed: ')
        out.write('timeout\nsecond line\n\n')
        out.write('partial')
        assert got == ['radio clip failed: timeout', 'second line']  # blank lines skipped, the partial one waits
    finally:
        log.removeHandler(handler)
