"""Lambda handler that appends batches of labels to the Google Sheet.

The function URL accepts a POST with a JSON body::

    {"key": "...", "rows": [{"cell_id": "...", "latitude": 0.0,
     "longitude": 0.0, "label": "not peat"}]}

CORS is handled by the function URL configuration, not here.
"""

import json
import os
from datetime import datetime, timezone
from functools import cache
from pathlib import Path
from typing import Any

import gspread

LABELS = {"not peat", "unknown"}


@cache
def known_keys() -> frozenset[str]:
    """Load the contributor keys built into the image by `make keys`."""
    path = Path(__file__).with_name("keys.json")
    return frozenset(json.loads(path.read_text()))


@cache
def labels_worksheet() -> gspread.Worksheet:
    """Open the ``labels`` worksheet once per Lambda container."""
    client = gspread.service_account(
        filename=Path(__file__).with_name("service-account.json")
    )
    return client.open_by_key(os.environ["SHEET_ID"]).worksheet("labels")


def response(status: int, body: dict[str, Any]) -> dict[str, Any]:
    """Build a function URL response with a JSON body."""
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }


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


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Validate a batch of labels and append it to the ``labels`` sheet."""
    try:
        payload = json.loads(event.get("body") or "")
        key = payload.get("key")
    except (ValueError, AttributeError):
        return response(400, {"error": "body must be a JSON object"})
    if not isinstance(key, str) or key not in known_keys():
        return response(403, {"error": "invalid key"})

    try:
        parsed = [parse_row(row) for row in payload["rows"]]
    except (ValueError, KeyError, TypeError) as error:
        return response(400, {"error": f"bad request: {error}"})

    # e.g. 2026-10-26T18:04:12.345Z; the page sends rows in click order, so
    # the latest server_time per (key, cell) is the current label
    server_time = (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )
    values = [
        [server_time, key, cell, lat, lon, label] for cell, lat, lon, label in parsed
    ]
    labels_worksheet().append_rows(values, value_input_option="RAW")
    return response(200, {"saved": len(values)})
