"""Tests for persistent receipt notification history."""

from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path

# Load pure integration helpers without importing Home Assistant.
package = types.ModuleType("custom_components.tgc1")
package.__path__ = [
    str(Path(__file__).resolve().parents[1] / "custom_components" / "tgc1")
]
sys.modules.setdefault("custom_components.tgc1", package)

from custom_components.tgc1.document_tracker import (  # noqa: E402
    async_load_seen_ids,
    async_save_seen_ids,
    find_new_documents,
    process_document,
    restore_seen_ids,
    serialize_seen_ids,
)


class ReceiptHistoryTests(unittest.IsolatedAsyncioTestCase):
    def test_first_run_baselines_existing_documents_without_notifications(self):
        documents = [{"id": 101}, {"id": 102}]
        seen_ids = restore_seen_ids(None, {"101", "102"})

        self.assertEqual(seen_ids, {"101", "102"})
        self.assertEqual(find_new_documents(documents, seen_ids), [])

    def test_new_document_is_detected_once(self):
        documents = [{"id": 101}, {"id": 103}]
        seen_ids = {"101"}

        new_documents = find_new_documents(documents, seen_ids)
        self.assertEqual(new_documents, [{"id": 103}])

        seen_ids.add(str(new_documents[0]["id"]))
        self.assertEqual(find_new_documents(documents, seen_ids), [])

    def test_repeated_refresh_does_not_return_seen_document_again(self):
        documents = [{"id": 201}]
        seen_ids = {"201"}

        self.assertEqual(find_new_documents(documents, seen_ids), [])
        self.assertEqual(find_new_documents(documents, seen_ids), [])

    def test_initialized_empty_history_survives_restart(self):
        stored = serialize_seen_ids(set())

        restored_ids = restore_seen_ids(stored, set())
        self.assertEqual(restored_ids, set())

        # A document arriving after restart must be treated as new, not baselined.
        documents = [{"id": 301}]
        self.assertEqual(find_new_documents(documents, restored_ids), documents)

    def test_legacy_non_empty_history_is_restored(self):
        self.assertEqual(
            restore_seen_ids({"ids": [1, "2"]}, {"3"}), {"1", "2"}
        )

    def test_invalid_persisted_ids_are_ignored(self):
        restored = restore_seen_ids(
            {"initialized": True, "ids": ["501", "broken", 0, -2, True]},
            {"600"},
        )
        self.assertEqual(restored, {"501"})

    def test_history_is_bounded_to_latest_500_sorted_ids(self):
        stored = serialize_seen_ids({str(item) for item in range(600)})

        self.assertIs(stored["initialized"], True)
        self.assertEqual(len(stored["ids"]), 500)
        self.assertEqual(stored["ids"][0], "100")
        self.assertEqual(stored["ids"][-1], "599")

    async def test_storage_load_failure_uses_current_documents_as_baseline(self):
        class BrokenStore:
            async def async_load(self):
                raise OSError("storage unavailable")

        with self.assertLogs("custom_components.tgc1.document_tracker", level="ERROR"):
            restored = await async_load_seen_ids(BrokenStore(), {"401", "402"})

        self.assertEqual(restored, {"401", "402"})

    async def test_storage_save_failure_does_not_raise_and_keeps_bounded_memory(self):
        class BrokenStore:
            async def async_save(self, _data):
                raise OSError("storage unavailable")

        seen_ids = {str(item) for item in range(600)}
        with self.assertLogs("custom_components.tgc1.document_tracker", level="ERROR"):
            saved = await async_save_seen_ids(BrokenStore(), seen_ids)

        self.assertFalse(saved)
        self.assertEqual(len(seen_ids), 500)
        self.assertEqual(min(seen_ids), "100")
        self.assertEqual(max(seen_ids), "599")

    async def test_storage_save_persists_initialized_empty_history(self):
        class MemoryStore:
            data = None

            async def async_save(self, data):
                self.data = data

        store = MemoryStore()
        self.assertTrue(await async_save_seen_ids(store, set()))
        self.assertEqual(store.data, {"initialized": True, "ids": []})

    def test_document_is_marked_seen_only_after_both_actions_succeed(self):
        seen_ids = set()
        calls = []

        process_document(
            501,
            seen_ids,
            lambda: calls.append("notification"),
            lambda: calls.append("event"),
        )

        self.assertEqual(calls, ["notification", "event"])
        self.assertEqual(seen_ids, {"501"})

    def test_notification_failure_does_not_mark_document_seen_or_fire_event(self):
        seen_ids = set()
        calls = []

        def fail_notification():
            calls.append("notification")
            raise OSError("notification unavailable")

        with self.assertRaises(OSError):
            process_document(
                502,
                seen_ids,
                fail_notification,
                lambda: calls.append("event"),
            )

        self.assertEqual(calls, ["notification"])
        self.assertEqual(seen_ids, set())

    def test_event_failure_does_not_mark_document_seen(self):
        seen_ids = set()
        calls = []

        def fail_event():
            calls.append("event")
            raise OSError("event unavailable")

        with self.assertRaises(OSError):
            process_document(
                503,
                seen_ids,
                lambda: calls.append("notification"),
                fail_event,
            )

        self.assertEqual(calls, ["notification", "event"])
        self.assertEqual(seen_ids, set())


if __name__ == "__main__":
    unittest.main()
