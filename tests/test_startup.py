import shlex
from pathlib import Path

from lightsout import main


def test_start_with_windows_registers_and_removes(monkeypatch):
    monkeypatch.setattr(main, 'RUN_VALUE', 'Lights Out (pytest)')  # never touch the real entry
    try:
        main.set_startup(True)
        cmd = main.startup_registered()
        assert cmd == main.startup_command()
        exe, script, flag = shlex.split(cmd, posix=False)
        assert Path(exe.strip('"')).name == 'pythonw.exe' and Path(exe.strip('"')).exists()
        assert Path(script.strip('"')).name == 'run.pyw' and Path(script.strip('"')).exists()
        assert flag == '--tray'  # Windows login: widgets only, no dashboard
    finally:
        main.set_startup(False)
    assert main.startup_registered() is None
    main.set_startup(False)  # removing twice is fine


def test_widget_off_every_screen_moves_onto_the_nearest():
    from PySide6.QtCore import QRect
    from lightsout.widgets import onto_screen
    screens = [QRect(0, 0, 2194, 1186), QRect(3840, 522, 1920, 1032)]  # 2nd monitor sits lower than the 1st
    assert onto_screen(QRect(4138, -4, 198, 260), screens) == (4138, 522)  # saved above it: dropped to its top edge
    assert onto_screen(QRect(5900, 2000, 200, 300), screens) == (5560, 1254)  # beyond the bottom-right corner
    assert onto_screen(QRect(4138, 600, 198, 260), screens) is None  # visible: stays put
    assert onto_screen(QRect(2050, 100, 198, 260), screens) is None  # half off an edge but centre on screen


def test_live_session_while_a_session_runs():
    from datetime import datetime, timedelta, timezone
    from lightsout.data import live_session
    fp1 = datetime(2026, 10, 9, 8, 30, tzinfo=timezone.utc)
    race = {'sessions': [('FP1', fp1), ('Sprint Quali', fp1 + timedelta(hours=4)), ('Race', fp1 + timedelta(days=2))]}
    assert live_session(race, fp1 + timedelta(minutes=20)) == 'FP1'
    assert live_session(race, fp1 + timedelta(minutes=61)) is None  # between sessions: countdown to Sprint Quali
    assert live_session(race, fp1 - timedelta(minutes=1)) is None
    assert live_session(race, fp1 + timedelta(days=2, hours=2)) == 'Race'
