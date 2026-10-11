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
