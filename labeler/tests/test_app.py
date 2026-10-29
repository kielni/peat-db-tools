"""Tests for the labels Lambda handler (Google Sheet mocked)."""

import json
import re
from typing import Any

import pytest

import app


class FakeWorksheet:
    """Record append_rows calls instead of writing to Google Sheets."""

    def __init__(self) -> None:
        self.rows: list[list[Any]] = []

    def append_rows(self, values: list[list[Any]], value_input_option: str) -> None:
        self.rows.extend(values)


@pytest.fixture
def sheet(monkeypatch: pytest.MonkeyPatch) -> FakeWorksheet:
    """Use one known key and a fake worksheet."""
    worksheet = FakeWorksheet()
    monkeypatch.setattr(app, "known_keys", lambda: frozenset({"good-key"}))
    monkeypatch.setattr(app, "labels_worksheet", lambda: worksheet)
    return worksheet


def label(**changes: Any) -> dict:
    """Build a valid label row."""
    return {
        "cell_id": "16717_-60478",
        "latitude": 37.33876,
        "longitude": -121.88608,
        "label": "not peat",
        **changes,
    }


def event(body: Any) -> dict:
    """Build a function URL event."""
    return {"body": body if isinstance(body, str) else json.dumps(body)}


def test_saves_batch(sheet: FakeWorksheet) -> None:
    """Valid rows are appended with server time and key."""
    rows = [label(), label(label="unknown")]
    result = app.handler(event({"key": "good-key", "rows": rows}), None)
    assert result["statusCode"] == 200
    assert json.loads(result["body"]) == {"saved": 2}
    assert len(sheet.rows) == 2
    server_time, key, cell, lat, lon, value = sheet.rows[1]
    assert (key, cell, value) == ("good-key", rows[1]["cell_id"], "unknown")
    # e.g. 2026-10-26T18:04:12.345Z
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z", server_time)


@pytest.mark.parametrize(
    "body",
    [
        {"key": "bad-key", "rows": [label()]},
        {"key": ["good-key"], "rows": [label()]},
        {"rows": [label()]},
        {"key": "bad-key", "rows": ["not a row"]},
    ],
)
def test_rejects_bad_key(sheet: FakeWorksheet, body: Any) -> None:
    """Missing or unknown keys get 403 before rows are checked."""
    result = app.handler(event(body), None)
    assert result["statusCode"] == 403
    assert sheet.rows == []


@pytest.mark.parametrize(
    "body",
    [
        "not json",
        ["not an object"],
        {"key": "good-key"},
        {"key": "good-key", "rows": [label(label="peat")]},
        {"key": "good-key", "rows": [label(latitude="north")]},
        {"key": "good-key", "rows": ["not a row"]},
    ],
)
def test_rejects_bad_body(sheet: FakeWorksheet, body: Any) -> None:
    """Malformed requests get 400 and nothing is written."""
    assert app.handler(event(body), None)["statusCode"] == 400
    assert sheet.rows == []
