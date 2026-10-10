"""Team radio transcripts: Whisper (faster-whisper, small.en) on the CPU, one clip at a time on a background thread.
Transcripts are cached in radio.json by clip URL, so a clip heard live is not transcribed again in a replay.
Without faster-whisper installed, text() is always None and race control just shows the play links."""
import functools
import json
import os
import queue
import tempfile
import threading

import requests

from . import data

MODEL = 'small.en'  # ~250 MB, downloaded once; base.en is faster but garbles noisy radio
PROMPT = ('Formula 1 team radio. Box, box. Copy. Track is drying, grip, tyres, deg, push, safety car, VSC, Overtake Mode, '
          'gap, pit window, lap, understood, mate.')  # steers Whisper towards F1 words (it heard "track" as "truck")
CACHE = data.APP_DIR / 'radio.json'

_lock = threading.Lock()
_done = None      # {url: transcript}; '' = nothing heard (beeps) or failed
_asked = set()
_jobs = queue.LifoQueue()  # newest request first: that's the one on top of the race control list
_worker = None


def _load():
    global _done
    if _done is None:
        try:
            _done = json.loads(CACHE.read_text(encoding='utf-8'))
        except (FileNotFoundError, ValueError):
            _done = {}


def text(url):
    """The clip's transcript, or None while it waits (queued on first ask)."""
    global _worker
    with _lock:
        _load()
        if url in _done:
            return _done[url]
        if url not in _asked:
            _asked.add(url)
            _jobs.put(url)
            if _worker is None:
                _worker = threading.Thread(target=_run, daemon=True)
                _worker.start()
    return None


@functools.cache
def available():
    try:
        import faster_whisper  # noqa: F401
        return True
    except ImportError:
        return False


def _run():
    try:
        from faster_whisper import WhisperModel
        model = WhisperModel(MODEL, device='cpu', compute_type='int8', cpu_threads=2)  # leaves cores for the map
    except Exception as e:  # not installed, or the model download failed: no transcripts this run
        print('radio transcripts off:', e)
        return
    while True:
        url = _jobs.get()
        try:
            r = requests.get(url, headers=data.UA, timeout=20)
            r.raise_for_status()
            fd, path = tempfile.mkstemp(suffix='.mp3')
            with os.fdopen(fd, 'wb') as f:
                f.write(r.content)
            try:
                segs, _ = model.transcribe(path, language='en', initial_prompt=PROMPT, vad_filter=True, beam_size=5)
                said = ' '.join(s.text.strip() for s in segs).strip()
            finally:
                os.remove(path)
        except Exception as e:  # one bad clip doesn't stop the rest
            print('radio clip failed:', url.rsplit('/', 1)[-1], e)
            said = ''
        with _lock:
            _done[url] = said
            try:
                data.APP_DIR.mkdir(parents=True, exist_ok=True)
                CACHE.write_text(json.dumps(_done), encoding='utf-8')
            except OSError as e:
                print('radio.json not saved:', e)
