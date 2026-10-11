import json

from boxbox import main


def test_old_single_opacity_becomes_text_and_background(tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'CONFIG', tmp_path / 'config.json')
    main.CONFIG.write_text(json.dumps({'opacity': 0.92, 'locked': True}))
    cfg = main.load_config()
    assert (cfg['text_opacity'], cfg['bg_opacity'], cfg['locked']) == (0.92, 0.81, True)  # same look as before
    assert 'opacity' not in cfg


def test_new_settings_are_kept_and_defaults_fill_in(tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'CONFIG', tmp_path / 'config.json')
    main.CONFIG.write_text(json.dumps({'text_opacity': 0.7, 'bg_opacity': 0.0, 'opacity': 0.5}))
    cfg = main.load_config()
    assert (cfg['text_opacity'], cfg['bg_opacity']) == (0.7, 0.0)
    main.CONFIG.unlink()
    assert (main.load_config()['text_opacity'], main.load_config()['bg_opacity']) == (1.0, 0.85)


def test_f1tv_check_says_why_without_crashing(tmp_path, monkeypatch):
    """No token, an expired one, or FastF1's private sign-in code changed: a message, never an exception or a wait."""
    from fastf1.internals import f1auth
    from boxbox import auth
    monkeypatch.setattr(f1auth, 'AUTH_DATA_FILE', tmp_path / 'token')
    assert auth.check() == (None, 'not signed in to F1 TV', None)
    (tmp_path / 'token').write_text('eyJhbGciOiJSUzI1NiJ9.eyJleHAiOjF9.c2ln')

    def expired(token, uri):
        import jwt
        raise jwt.ExpiredSignatureError('Signature has expired')
    monkeypatch.setattr(f1auth, '_verify_jwt', expired)
    assert auth.check()[:2] == (None, 'F1 TV sign-in expired: sign in again')
    monkeypatch.delattr(f1auth, '_verify_jwt')
    token, text, _ = auth.check()
    assert token is None and "FastF1's sign-in code changed (no _verify_jwt)" in text
