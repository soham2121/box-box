import shlex
from pathlib import Path

from f1tracker import main


def test_start_with_windows_registers_and_removes(monkeypatch):
    monkeypatch.setattr(main, 'RUN_VALUE', 'F1 tracker (pytest)')  # never touch the real entry
    try:
        main.set_startup(True)
        cmd = main.startup_registered()
        assert cmd == main.startup_command()
        exe, script = shlex.split(cmd, posix=False)
        assert Path(exe.strip('"')).name == 'pythonw.exe' and Path(exe.strip('"')).exists()
        assert Path(script.strip('"')).name == 'run.pyw' and Path(script.strip('"')).exists()
    finally:
        main.set_startup(False)
    assert main.startup_registered() is None
    main.set_startup(False)  # removing twice is fine
