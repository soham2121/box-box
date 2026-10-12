"""data.fetch: the disk cache every standings / schedule / archive request goes through. requests is faked."""
import os
import time

import pytest
import requests

from lightsout import data

URL = 'https://api.jolpi.ca/ergast/f1/current.json'


class Net:
    """requests.get stand-in: answers with `body`, or fails like a dropped connection; counts calls."""

    def __init__(self, monkeypatch):
        self.body, self.status, self.down, self.urls = b'{"v": 1}', 200, False, []
        monkeypatch.setattr(data.requests, 'get', self.get)

    def get(self, url, headers=None, timeout=None):
        self.urls.append(url)
        if self.down:
            raise requests.ConnectionError('offline')
        r = requests.Response()
        r.status_code, r._content = (404 if url.startswith(data.ARCHIVE) and self.status == 404 else 200), self.body
        return r


@pytest.fixture
def net(tmp_path, monkeypatch):
    monkeypatch.setattr(data, 'CACHE', tmp_path)
    return Net(monkeypatch)


def age(seconds):
    f = next(data.CACHE.iterdir())
    os.utime(f, (time.time() - seconds, time.time() - seconds))


def test_fresh_cache_is_used_without_the_network(net):
    assert data.fetch(URL, 600) == {'v': 1}
    net.body = b'{"v": 2}'
    assert data.fetch(URL, 600) == {'v': 1} and len(net.urls) == 1


def test_stale_cache_is_refreshed(net):
    data.fetch(URL, 600)
    age(601)
    net.body = b'{"v": 2}'
    assert data.fetch(URL, 600) == {'v': 2} and len(net.urls) == 2
    assert data.fetch(URL, 600) == {'v': 2} and len(net.urls) == 2  # and saved


def test_offline_falls_back_to_stale_cache_and_fails_without_one(net):
    data.fetch(URL, 600)
    age(10_000)
    net.down = True
    assert data.fetch(URL, 600) == {'v': 1}  # old standings beat an error in the widget
    with pytest.raises(requests.ConnectionError):
        data.fetch(URL + '?other', 600)


def test_history_is_cached_forever(net):
    data.fetch(URL, None)
    age(365 * 86400)
    assert data.fetch(URL, None) == {'v': 1} and len(net.urls) == 1


def test_archive_falls_back_to_the_mirror_and_byte_order_mark_is_dropped(net):
    net.status, net.body = 404, '﻿{"Status": "Offline"}'.encode('utf-8')  # F1's archive files start with a BOM
    assert data.fetch(data.ARCHIVE + '2022/Index.json', None) == {'Status': 'Offline'}
    assert net.urls == [data.ARCHIVE + '2022/Index.json', data.MIRROR + '2022/Index.json']
