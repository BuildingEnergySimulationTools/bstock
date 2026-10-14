"""Shared fixtures.

Connector tests never reach the network: they read the real responses recorded in ``tests/data``
(``recorded``) and serve them through a fake ``requests`` session (``fake_session``) or a fake
client. Tests calling the real APIs carry the ``network`` marker.
"""

import json
from pathlib import Path

import pytest

DATA_DIR = Path(__file__).parent / "data"


@pytest.fixture
def recorded():
    """Return a loader of the recorded API responses: ``recorded("rnb/building_….json")``."""

    def load(path):
        return json.loads((DATA_DIR / path).read_text(encoding="utf-8"))

    return load


class FakeResponse:
    def __init__(self, status_code=200, body=None, headers=None, url="https://fake/"):
        self.status_code = status_code
        self._body = body
        self.headers = headers or {}
        self.url = url
        self.text = body if isinstance(body, str) else json.dumps(body)

    def json(self):
        if isinstance(self._body, str):
            raise ValueError("not JSON")
        return self._body


class FakeSession:
    """Serve queued responses (or raise queued exceptions) and record the requests."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append({"url": url, "params": params, "headers": headers, "timeout": timeout})
        item = self.responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        if isinstance(item, FakeResponse):
            item.url = url
            return item
        return FakeResponse(200, item, url=url)


@pytest.fixture
def fake_session():
    """Return a factory: ``fake_session(body, FakeResponse(429), ConnectionError(), ...)``.

    Plain values are served as 200 JSON responses.
    """
    return FakeSession


@pytest.fixture
def fake_response():
    """Return the :class:`FakeResponse` class, to queue errors in a fake session."""
    return FakeResponse
