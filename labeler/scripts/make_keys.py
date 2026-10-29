"""Append 20 new contributor keys to ``keys``, then write lambda/keys.json.

lambda/keys.json holds every key in the sheet and is built into the Lambda
image. SHEET_ID and SITE_URL come from local.env, exported and checked by make.
"""

import json
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path

import gspread

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    """Append 20 new keys with blank emails and sync keys.json."""
    site_url = os.environ["SITE_URL"].rstrip("/")
    created = datetime.now(timezone.utc).date().isoformat()
    client = gspread.service_account(filename=ROOT / "service-account.json")
    worksheet = client.open_by_key(os.environ["SHEET_ID"]).worksheet("keys")
    rows = []
    for _ in range(20):
        key = secrets.token_urlsafe(12)  # 16 URL-safe characters
        rows.append([key, "", f"{site_url}/?key={key}", created])
    # RAW so a key starting with "-" or "+" is never read as a formula
    worksheet.append_rows(rows, value_input_option="RAW")

    # numericise_ignore keeps every key a string
    records = worksheet.get_all_records(numericise_ignore=["all"])
    keys = sorted(row["key"] for row in records if row["key"])
    keys_file = ROOT / "lambda" / "keys.json"
    keys_file.write_text(json.dumps(keys, indent=1) + "\n")
    print(f"added {len(rows)} keys; {len(keys)} keys in {keys_file}")


if __name__ == "__main__":
    main()
