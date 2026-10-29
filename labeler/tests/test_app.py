"""Tests for the labels Lambda handler (Google Sheet mocked)."""

import json
import re
from typing import Any

import gspread
import pytest

import app

# e.g. 2026-10-26T18:04:12.345Z
TIME_PATTERN = r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z"


class FakeWorksheet:
    """Keep rows in memory instead of reading and writing Google Sheets."""

    def __init__(self, rows: list[list[Any]] | None = None) -> None:
        self.rows: list[list[Any]] = rows or []

    def append_rows(self, values: list[list[Any]], value_input_option: str) -> None:
        self.rows.extend(values)

    def get_all_values(self) -> list[list[Any]]:
        return self.rows

    def row_values(self, row: int) -> list[Any]:
        return self.rows[row - 1]

    def find(self, query: str, in_column: int) -> gspread.Cell | None:
        for number, row in enumerate(self.rows, start=1):
            if row[in_column - 1] == query:
                return gspread.Cell(number, in_column, query)
        return None

    def update_cells(self, cells: list[gspread.Cell]) -> None:
        for cell in cells:
            row = self.rows[cell.row - 1]
            row.extend([""] * (cell.col - len(row)))
            row[cell.col - 1] = cell.value


@pytest.fixture
def sheets(monkeypatch: pytest.MonkeyPatch) -> dict[str, FakeWorksheet]:
    """Use one known key, a show-all key, and fake worksheets."""
    worksheets = {
        "labels": FakeWorksheet(
            [["server_time", "key", "cell_id", "latitude", "longitude", "label"]]
        ),
        "keys": FakeWorksheet(
            [
                ["key", "email", "url", "created", "completed"],
                ["other-key", "", "", "2026-10-26", ""],
                ["good-key", "", "", "2026-10-26", ""],
            ]
        ),
    }
    monkeypatch.setattr(app, "known_keys", lambda: frozenset({"good-key", "other-key"}))
    monkeypatch.setenv("SHOW_ALL_KEY", "all-key")
    monkeypatch.setattr(app, "worksheet", worksheets.__getitem__)
    return worksheets


def label(**changes: Any) -> dict:
    """Build a valid label row."""
    return {
        "cell_id": "17922_-58025",
        "latitude": 37.33854,
        "longitude": -120.88729,
        "label": "not peat",
        **changes,
    }


def event(body: Any) -> dict:
    """Build a function URL event."""
    return {"body": body if isinstance(body, str) else json.dumps(body)}


def call(body: Any) -> tuple[int, Any]:
    """Run the handler and return its status and decoded body."""
    result = app.handler(event(body), None)
    return result["statusCode"], json.loads(result["body"])


def test_saves_batch(sheets: dict[str, FakeWorksheet]) -> None:
    """Valid rows are appended with server time and key; save is the default."""
    rows = [label(), label(label="peat"), label(label="unknown")]
    assert call({"key": "good-key", "rows": rows}) == (200, {"saved": 3})
    saved = sheets["labels"].rows[1:]
    assert [row[5] for row in saved] == ["not peat", "peat", "unknown"]
    server_time, key, cell, lat, lon, value = saved[2]
    assert (key, cell, value) == ("good-key", rows[2]["cell_id"], "unknown")
    assert re.fullmatch(TIME_PATTERN, server_time)


@pytest.mark.parametrize(
    "body",
    [
        {"key": "bad-key", "rows": [label()]},
        {"key": ["good-key"], "rows": [label()]},
        {"rows": [label()]},
        {"key": "", "action": "load"},
        {"key": "bad-key", "rows": ["not a row"]},
    ],
)
def test_rejects_bad_key(sheets: dict[str, FakeWorksheet], body: Any) -> None:
    """Missing or unknown keys get 403 before rows are checked."""
    assert call(body)[0] == 403
    assert len(sheets["labels"].rows) == 1


def test_empty_show_all_key_matches_nothing(
    sheets: dict[str, FakeWorksheet], monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unset SHOW_ALL_KEY does not let an empty key in."""
    monkeypatch.setenv("SHOW_ALL_KEY", "")
    assert call({"key": "", "action": "load"})[0] == 403


@pytest.mark.parametrize(
    "body",
    [
        "not json",
        ["not an object"],
        {"key": "good-key"},
        {"key": "good-key", "action": "delete"},
        {"key": "good-key", "action": ["load"]},
        {"key": "good-key", "rows": [label(label="maybe")]},
        {"key": "good-key", "rows": [label(latitude="north")]},
        {"key": "good-key", "rows": ["not a row"]},
    ],
)
def test_rejects_bad_body(sheets: dict[str, FakeWorksheet], body: Any) -> None:
    """Malformed requests get 400 and nothing is written."""
    assert call(body)[0] == 400
    assert len(sheets["labels"].rows) == 1


def save_history() -> None:
    """Save labels from two keys, changing some cells along the way."""
    call({"key": "good-key", "rows": [label(cell_id="a"), label(cell_id="b")]})
    call({"key": "other-key", "rows": [label(cell_id="a", label="peat")]})
    call(
        {
            "key": "good-key",
            "rows": [label(cell_id="a", label="unknown"), label(cell_id="a")],
        }
    )
    call({"key": "good-key", "rows": [label(cell_id="b", label="unknown")]})


def test_load_returns_own_latest_labels(sheets: dict[str, FakeWorksheet]) -> None:
    """A key gets the last label per cell it saved, unknown included."""
    save_history()
    status, body = call({"key": "good-key", "action": "load"})
    assert (status, body["show_all"]) == (200, False)
    assert sorted(body["labels"], key=lambda row: row["cell_id"]) == [
        {"cell_id": "a", "label": "not peat"},
        {"cell_id": "b", "label": "unknown"},
    ]


def test_load_with_show_all_key_returns_every_key(
    sheets: dict[str, FakeWorksheet],
) -> None:
    """The show-all key gets the last label per (key, cell) for all keys."""
    save_history()
    status, body = call({"key": "all-key", "action": "load"})
    assert (status, body["show_all"]) == (200, True)
    assert sorted(body["labels"], key=lambda row: (row["cell_id"], row["label"])) == [
        {"cell_id": "a", "label": "not peat"},
        {"cell_id": "a", "label": "peat"},
        {"cell_id": "b", "label": "unknown"},
    ]


def test_done_writes_completed_time(sheets: dict[str, FakeWorksheet]) -> None:
    """Done writes the time to the key's completed column, again on repeat."""
    assert call({"key": "good-key", "action": "done"}) == (200, {"completed": True})
    first = sheets["keys"].rows[2][4]
    assert re.fullmatch(TIME_PATTERN, first)
    assert sheets["keys"].rows[1][4] == ""
    call({"key": "good-key", "action": "done"})
    assert re.fullmatch(TIME_PATTERN, sheets["keys"].rows[2][4])


def test_done_with_key_missing_from_sheet(sheets: dict[str, FakeWorksheet]) -> None:
    """The show-all key has no row in the keys sheet."""
    assert call({"key": "all-key", "action": "done"})[0] == 404
