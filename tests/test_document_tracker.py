"""Tests for persistent receipt notification history."""

from __future__ import annotations

import sys
import types
from pathlib import Path

# Load pure integration helpers without importing Home Assistant.
package = types.ModuleType("custom_components.tgc1")
package.__path__ = [
    str(Path(__file__).resolve().parents[1] / "custom_components" / "tgc1")
]
sys.modules.setdefault("custom_components.tgc1", package)

from custom_components.tgc1.document_tracker import (  # noqa: E402
    find_new_documents,
    restore_seen_ids,
    serialize_seen_ids,
)


def test_first_run_baselines_existing_documents_without_notifications() -> None:
    documents = [{"id": 101}, {"id": 102}]
    seen_ids = restore_seen_ids(None, {"101", "102"})

    assert seen_ids == {"101", "102"}
    assert find_new_documents(documents, seen_ids) == []


def test_new_document_is_detected_once() -> None:
    documents = [{"id": 101}, {"id": 103}]
    seen_ids = {"101"}

    new_documents = find_new_documents(documents, seen_ids)
    assert new_documents == [{"id": 103}]

    seen_ids.add(str(new_documents[0]["id"]))
    assert find_new_documents(documents, seen_ids) == []


def test_repeated_refresh_does_not_return_seen_document_again() -> None:
    documents = [{"id": 201}]
    seen_ids = {"201"}

    assert find_new_documents(documents, seen_ids) == []
    assert find_new_documents(documents, seen_ids) == []


def test_initialized_empty_history_survives_restart() -> None:
    stored = serialize_seen_ids(set())

    restored_ids = restore_seen_ids(stored, set())
    assert restored_ids == set()

    # A document arriving after restart must be treated as new, not baselined.
    documents = [{"id": 301}]
    assert find_new_documents(documents, restored_ids) == documents


def test_legacy_non_empty_history_is_restored() -> None:
    assert restore_seen_ids({"ids": [1, "2"]}, {"3"}) == {"1", "2"}


def test_history_is_bounded_to_latest_500_sorted_ids() -> None:
    stored = serialize_seen_ids({str(item) for item in range(600)})

    assert stored["initialized"] is True
    assert len(stored["ids"]) == 500
    assert stored["ids"][0] == "100"
    assert stored["ids"][-1] == "599"
