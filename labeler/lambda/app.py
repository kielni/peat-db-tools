"""Lambda handler that saves and loads labels in the Google Sheet.

The function URL accepts a POST with a JSON body. ``action`` picks what to do
and defaults to ``save``::

    {"key": "...", "action": "save", "rows": [{"cell_id": "...",
     "latitude": 0.0, "longitude": 0.0, "label": "not peat"}]}
    {"key": "...", "action": "load"}
    {"key": "...", "action": "done"}

CORS is handled by the function URL configuration, not here.
"""

import json
import os
from datetime import datetime, timezone
from functools import cache
from pathlib import Path
from typing import Any

import gspread

LABELS = {"peat", "not peat", "unknown"}


@cache
def known_keys() -> frozenset[str]:
    """Load the contributor keys built into the image by `make keys`."""
    path = Path(__file__).with_name("keys.json")
    return frozenset(json.loads(path.read_text()))


def show_all_key() -> str:
    """Return the key that loads every contributor's labels, or unset."""
    return os.environ.get("SHOW_ALL_KEY", "unset") or "unset"


@cache
def worksheet(title: str) -> gspread.Worksheet:
    """Open a worksheet of the sheet once per Lambda container."""
    client = gspread.service_account(
        filename=Path(__file__).with_name("service-account.json")
    )
    return client.open_by_key(os.environ["SHEET_ID"]).worksheet(title)


def response(status: int, body: dict[str, Any]) -> dict[str, Any]:
    """Build a function URL response with a JSON body."""
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }


def now() -> str:
    """Return the current UTC time, e.g. 2026-10-26T18:04:12.345Z."""
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def parse_row(row: dict[str, Any]) -> list[Any]:
    """Return one label from the page as sheet values.

    Raise ValueError, KeyError or TypeError if the row is malformed.
    """
    if row["label"] not in LABELS:
        raise ValueError(f"bad label: {row['label']!r}")
    return [
        row["cell_id"],
        float(row["latitude"]),
        float(row["longitude"]),
        row["label"],
    ]


def save(key: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Validate a batch of labels and append it to the ``labels`` sheet."""
    try:
        parsed = [parse_row(row) for row in payload["rows"]]
    except (ValueError, KeyError, TypeError) as error:
        return response(400, {"error": f"bad request: {error}"})

    # The page sends rows in click order and appends keep that order, so the
    # last row per (key, cell) in the sheet is the current label
    server_time = now()
    values = [
        [server_time, key, cell, lat, lon, label] for cell, lat, lon, label in parsed
    ]
    worksheet("labels").append_rows(values, value_input_option="RAW")
    return response(200, {"saved": len(values)})


def load(key: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Return the current label per (key, cell), for this key or for all keys.

    Labels of every key are returned for the show-all key; ``unknown`` labels
    are included so the page can draw cleared cells.
    """
    show_all = key == show_all_key()
    latest: dict[tuple[str, str], str] = {}
    # Columns: server_time, key, cell_id, latitude, longitude, label. Checking
    # the label also skips a header row.
    for row in worksheet("labels").get_all_values():
        row_key, cell, label = row[1], row[2], row[5]
        if label in LABELS and (show_all or row_key == key):
            latest[row_key, cell] = label  # later rows win
    labels = [{"cell_id": cell, "label": label} for (_, cell), label in latest.items()]
    return response(200, {"show_all": show_all, "labels": labels})


def done(key: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Write the time to the key's ``completed`` column in the ``keys`` sheet."""
    keys = worksheet("keys")
    found = keys.find(key, in_column=1)
    if found is None:
        return response(404, {"error": "key is not in the keys sheet"})
    column = keys.row_values(1).index("completed") + 1
    # update_cells writes RAW, so the time stays text like in the labels sheet
    keys.update_cells([gspread.Cell(found.row, column, now())])
    return response(200, {"completed": True})


ACTIONS = {"save": save, "load": load, "done": done}


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Check the key, then run the requested action."""
    try:
        payload = json.loads(event.get("body") or "")
        key = payload.get("key")
    except (ValueError, AttributeError):
        return response(400, {"error": "body must be a JSON object"})
    if not isinstance(key, str) or not (key in known_keys() or key == show_all_key()):
        return response(403, {"error": "invalid key"})

    name = payload.get("action", "save")
    if not isinstance(name, str) or name not in ACTIONS:
        return response(400, {"error": f"unknown action: {name!r}"})
    return ACTIONS[name](key, payload)
