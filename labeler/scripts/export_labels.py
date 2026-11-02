"""Export each contributor's current labels from the sheet to a CSV file.

The ``labels`` tab is append-only, so the last row per (key, cell) is that
contributor's current label; cells whose current label is ``unknown`` were
cleared and are left out. Each row gets the contributor's email from the
``keys`` tab. SHEET_ID comes from local.env, exported by make.

Usage: export_labels.py [OUTPUT]   (default: data/labels.csv at the repo root)
"""

import csv
import os
import sys
from pathlib import Path

import gspread

ROOT = Path(__file__).resolve().parent.parent
LABELS = {"peat", "not peat", "unknown"}
HEADER = [
    "server_time",
    "key",
    "email",
    "cell_id",
    "latitude",
    "longitude",
    "label",
]


def latest_labels(rows: list[list[str]]) -> list[list[str]]:
    """Return the last row per (key, cell), dropping cleared cells.

    Rows are ``server_time, key, cell_id, latitude, longitude, label`` in
    sheet order. Checking the label also skips a header row.
    """
    latest: dict[tuple[str, str], list[str]] = {}
    for row in rows:
        if row[5] in LABELS:
            latest[row[1], row[2]] = row  # later rows win
    return [row for row in latest.values() if row[5] != "unknown"]


def add_emails(rows: list[list[str]], emails: dict[str, str]) -> list[list[str]]:
    """Insert each row's contributor email after its key ("" if unknown)."""
    return [row[:2] + [emails.get(row[1], "")] + row[2:] for row in rows]


def main() -> None:
    """Read the labels and keys tabs and write the current labels as CSV."""
    output = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT.parent / "data/labels.csv"
    client = gspread.service_account(filename=ROOT / "service-account.json")
    sheet = client.open_by_key(os.environ["SHEET_ID"])
    # numericise_ignore keeps every key a string
    records = sheet.worksheet("keys").get_all_records(numericise_ignore=["all"])
    emails = {row["key"]: row["email"] for row in records}
    rows = add_emails(latest_labels(sheet.worksheet("labels").get_all_values()), emails)

    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(HEADER)
        writer.writerows(rows)
    print(f"wrote {len(rows)} labels to {output}")


if __name__ == "__main__":
    main()
