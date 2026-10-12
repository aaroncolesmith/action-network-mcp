import json
from datetime import date
from pathlib import Path

import pytest

from action_network import service as service_mod
from action_network.client import ActionNetworkClient
from action_network.history import HistoryStore
from action_network.service import OddsService, set_service

FIXTURES = Path(__file__).parent / "fixtures"
TODAY = date(2026, 10, 11)


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
        self.headers = {}

    def json(self):
        return self._payload


class FakeSession:
    """Routes Action Network URLs to local fixtures and counts upstream calls."""

    def __init__(self):
        self.headers = {}
        self.calls = []
        self.scoreboard = json.loads((FIXTURES / "scoreboard_nfl.json").read_text())
        self.futures = json.loads((FIXTURES / "futures_mvp.json").read_text())
        self.fail_with = None

    def get(self, url, timeout=None):
        self.calls.append(url)
        if self.fail_with:
            return FakeResponse({}, self.fail_with)
        if "/scoreboard/nfl" in url:
            return FakeResponse(self.scoreboard if "date=20261011" in url else {"games": []})
        if "/scoreboard/" in url:
            return FakeResponse({"games": []})
        if "/leagues/1/futures/available" in url:
            return FakeResponse({"futures": [{"type": "nfl_futures_mvp", "name": "2026-27 NFL MVP"},
                                             {"type": "nfl_futures_super_bowl", "name": "Super Bowl Winner"}]})
        if "/futures/available" in url:
            return FakeResponse({"futures": []})
        if "/leagues/1/futures/nfl_futures_mvp" in url:
            return FakeResponse(self.futures)
        return FakeResponse({}, 404)


@pytest.fixture
def session():
    return FakeSession()


@pytest.fixture
def svc(session, tmp_path, monkeypatch):
    monkeypatch.setattr(service_mod, "site_today", lambda: TODAY)
    client = ActionNetworkClient(session=session, min_interval=0, max_retries=1)
    s = OddsService(client=client, history=HistoryStore(str(tmp_path / "hist.sqlite")))
    set_service(s)
    yield s
    set_service(None)
