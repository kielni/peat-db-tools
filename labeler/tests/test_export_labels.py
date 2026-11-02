"""Tests for reducing the labels sheet to current labels with emails."""

from export_labels import add_emails, latest_labels

HEADER = ["server_time", "key", "cell_id", "latitude", "longitude", "label"]


def row(time: str, key: str, cell: str, label: str) -> list[str]:
    """Build a labels-sheet row with fixed coordinates."""
    return [time, key, cell, "1.5", "2.5", label]


def test_later_label_wins() -> None:
    rows = [HEADER, row("t1", "a", "1_1", "peat"), row("t2", "a", "1_1", "not peat")]
    assert latest_labels(rows) == [row("t2", "a", "1_1", "not peat")]


def test_cleared_cell_is_dropped() -> None:
    rows = [
        row("t1", "a", "1_1", "peat"),
        row("t2", "a", "1_1", "not peat"),
        row("t3", "a", "1_1", "unknown"),
    ]
    assert latest_labels(rows) == []


def test_relabel_after_clear_is_kept() -> None:
    rows = [row("t1", "a", "1_1", "unknown"), row("t2", "a", "1_1", "peat")]
    assert latest_labels(rows) == [row("t2", "a", "1_1", "peat")]


def test_header_and_bad_labels_are_skipped() -> None:
    rows = [HEADER, row("t1", "a", "1_1", "maybe")]
    assert latest_labels(rows) == []


def test_contributors_are_kept_separate() -> None:
    rows = [row("t1", "a", "1_1", "peat"), row("t2", "b", "1_1", "not peat")]
    assert latest_labels(rows) == rows


def test_add_emails() -> None:
    rows = [row("t1", "a", "1_1", "peat"), row("t2", "b", "1_1", "peat")]
    assert add_emails(rows, {"a": "a@example.com"}) == [
        ["t1", "a", "a@example.com", "1_1", "1.5", "2.5", "peat"],
        ["t2", "b", "", "1_1", "1.5", "2.5", "peat"],
    ]
