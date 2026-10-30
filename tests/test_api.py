"""Unit tests for the TGC-1 API client."""

from __future__ import annotations

import asyncio
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

from custom_components.tgc1.api import (  # noqa: E402
    Tgc1ApiError,
    Tgc1AuthError,
    Tgc1Client,
)


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


class FakeResponse:
    def __init__(self, status: int, data=None) -> None:
        self.status = status
        self.data = data

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    def raise_for_status(self) -> None:
        if self.status >= 400:
            raise AssertionError(f"Unexpected HTTP status {self.status}")

    async def json(self, **_kwargs):
        return self.data


class ConcurrentRefreshSession:
    """Make two old-token requests receive 401 at the same time."""

    def __init__(self) -> None:
        self.old_requests = 0
        self.request_count = 0
        self.login_count = 0
        self.both_old_requests = asyncio.Event()

    async def post(self, *_args, **_kwargs):
        self.login_count += 1
        return FakeResponse(200, {"accessToken": "token-new"})

    def request(self, _method, _url, **kwargs):
        self.request_count += 1
        token = kwargs["headers"]["Authorization"]
        if token == "Bearer token-old":
            async def wait_for_other_request():
                self.old_requests += 1
                if self.old_requests == 2:
                    self.both_old_requests.set()
                await self.both_old_requests.wait()
                return FakeResponse(401)
            return AsyncResponse(wait_for_other_request)
        return AsyncResponse(lambda: asyncio.sleep(0, result=FakeResponse(
            200, [{"id": 1}]
        )))


class AsyncResponse:
    def __init__(self, factory) -> None:
        self.factory = factory
        self.response = None

    async def __aenter__(self):
        self.response = await self.factory()
        return self.response

    async def __aexit__(self, *_args):
        return None


class PostUnauthorizedSession:
    def __init__(self) -> None:
        self.request_count = 0

    def request(self, *_args, **_kwargs):
        self.request_count += 1
        return AsyncResponse(lambda: asyncio.sleep(0, result=FakeResponse(401)))


class ApiAuthenticationTests(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_stale_401_does_not_clear_refreshed_token(self):
        session = ConcurrentRefreshSession()
        client = Tgc1Client(session, "user", "password")
        client._token = "token-old"

        first, second = await asyncio.gather(
            client._async_request("GET", "/devices"),
            client._async_request("GET", "/devices"),
        )

        self.assertEqual(first, [{"id": 1}])
        self.assertEqual(second, [{"id": 1}])
        self.assertEqual(session.login_count, 1)
        self.assertEqual(session.request_count, 4)
        self.assertEqual(client._token, "token-new")

    async def test_post_is_not_retried_after_401(self):
        session = PostUnauthorizedSession()
        client = Tgc1Client(session, "user", "password")
        client._token = "token-old"

        with self.assertRaises(Tgc1AuthError):
            await client._async_request(
                "POST", "/reading", json_body={"value": 123}
            )

        self.assertEqual(session.request_count, 1)
        self.assertIsNone(client._token)


if __name__ == "__main__":
    unittest.main()
