"""Unit tests for the TGC-1 API response validation."""

from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path

# Load the API module without importing the Home Assistant integration package.
package = types.ModuleType("custom_components.tgc1")
package.__path__ = [
    str(Path(__file__).resolve().parents[1] / "custom_components" / "tgc1")
]
sys.modules.setdefault("custom_components.tgc1", package)

from custom_components.tgc1.api import Tgc1ApiError, Tgc1Client  # noqa: E402


class ApiResponseValidationTests(unittest.TestCase):
    def test_records_are_validated_and_ids_normalized(self) -> None:
        records = [{"id": "123", "name": "meter"}]

        result = Tgc1Client._require_records(records, "devices")

        self.assertEqual(result[0]["id"], 123)

    def test_non_list_response_is_rejected(self) -> None:
        with self.assertRaises(Tgc1ApiError):
            Tgc1Client._require_records({"items": []}, "devices")

    def test_non_object_record_is_rejected(self) -> None:
        with self.assertRaises(Tgc1ApiError):
            Tgc1Client._require_records([None], "devices")

    def test_record_without_id_is_rejected(self) -> None:
        with self.assertRaises(Tgc1ApiError):
            Tgc1Client._require_records([{"name": "meter"}], "devices")

    def test_non_numeric_id_is_rejected(self) -> None:
        with self.assertRaises(Tgc1ApiError):
            Tgc1Client._require_records([{"id": "meter-1"}], "devices")

    def test_non_positive_id_is_rejected(self) -> None:
        with self.assertRaises(Tgc1ApiError):
            Tgc1Client._require_records([{"id": "0"}], "devices")

    def test_float_id_is_rejected(self) -> None:
        with self.assertRaises(Tgc1ApiError):
            Tgc1Client._require_records([{"id": 12.5}], "devices")


if __name__ == "__main__":
    unittest.main()
