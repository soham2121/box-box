"""F1 TV sign-in. FastF1 keeps it in private code (fastf1.internals.f1auth) that can change in any release
(requirements.txt pins 3.8.3). Everything that touches it is here, so a change shows up as a clear message in the app
(live falls back to timing without GPS), never as a crash or a hang."""
import logging
import socket
import threading
import webbrowser
from datetime import datetime, timezone
from http.server import HTTPServer

SIGNIN_HELP = ('Needs the "FastF1 Companion" browser extension (the page links it). Log in at formula1.com, '
               'then click "Connect FastF1" on the page the extension opens.')
log = logging.getLogger('boxbox.auth')
_signin_port = None  # port of a sign-in still waiting for the browser


def _f1auth():
    from fastf1.internals import f1auth
    for name in ('AUTH_DATA_FILE', 'JWKS_URL', '_verify_jwt', 'AuthHandler', '_auth_finished'):
        if not hasattr(f1auth, name):
            raise ImportError(f"FastF1's sign-in code changed (no {name})")
    return f1auth


def check():
    """(token or None, one line for the user, when it expires or None). The token only if it verifies now (F1's signature, not expired);
    never asks to sign in: FastF1's own get_auth_token() opens a browser and waits when the token has expired, which
    would stall the live feed."""
    try:
        f1auth = _f1auth()
    except ImportError as e:
        log.warning('%s', e)
        return None, f'F1 TV sign-in unavailable: {e}. Live runs without GPS.', None
    try:
        token = f1auth.AUTH_DATA_FILE.read_text().strip()
    except OSError:
        return None, 'not signed in to F1 TV', None
    if not token:
        return None, 'not signed in to F1 TV', None
    try:
        payload = f1auth._verify_jwt(token, f1auth.JWKS_URL)
    except Exception as e:  # expired, bad signature, or F1's key server unreachable
        kind = type(e).__name__
        log.info('F1 TV token not usable: %s', kind)
        return None, ('F1 TV sign-in expired: sign in again' if kind == 'ExpiredSignatureError'
                      else f'F1 TV token could not be checked ({kind}): sign in again, or check the connection'), None
    exp = payload.get('exp') if isinstance(payload, dict) else None
    exp = datetime.fromtimestamp(exp, timezone.utc) if exp else None
    return token, 'signed in to F1 TV' + (exp.astimezone().strftime(' until %d %b') if exp else ''), exp


def token():
    return check()[0]


def sign_in(timeout=1800):
    """F1 TV sign-in through FastF1's browser flow: the FastF1 Companion extension reads the formula1.com login cookie
    and POSTs it to http://localhost:<port>/auth. Blocks until that arrives or `timeout` s (installing the extension
    and logging in can take a while). Returns True, or a string saying what went wrong."""
    global _signin_port
    try:
        f1auth = _f1auth()
    except ImportError as e:
        return str(e)
    if _signin_port:  # clicked again while waiting: same server, reopen the page
        webbrowser.open(f'https://f1login.fastf1.dev?port={_signin_port}')
        f1auth._auth_finished.wait(timeout)
        return token() is not None or 'sign-in did not finish'

    class Quiet(f1auth.AuthHandler):
        def log_message(self, *args):  # logs to sys.stderr, which is None under pythonw.exe: the reply never went out
            pass

    class V6(HTTPServer):
        address_family = socket.AF_INET6

    servers = [HTTPServer(('127.0.0.1', 0), Quiet)]
    _signin_port = port = servers[0].server_port
    try:  # the extension calls 'localhost', which can resolve to ::1 first
        servers.append(V6(('::1', port), Quiet))
    except OSError:
        pass
    for s in servers:
        threading.Thread(target=s.serve_forever, daemon=True).start()
    f1auth._auth_finished.clear()
    f1auth._subscription_token = None
    webbrowser.open(f'https://f1login.fastf1.dev?port={port}')
    try:
        arrived = f1auth._auth_finished.wait(timeout)
    finally:
        for s in servers:
            s.shutdown()
            s.server_close()
        _signin_port = None
    if not arrived:
        return f'no answer from the browser within {timeout // 60} min'
    if not getattr(f1auth, '_subscription_token', None):
        return 'logged in, but the account has no F1 TV subscription token'
    f1auth.AUTH_DATA_FILE.write_text(f1auth._subscription_token)
    return token() is not None or check()[1]
