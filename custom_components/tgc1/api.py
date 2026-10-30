"""Async API client for the TGC-1 personal cabinet."""

from __future__ import annotations

import asyncio
from typing import Any

import aiohttp

from .const import (
    CREATE_READING_URL,
    DEVICE_URL,
    DOCUMENTS_URL,
    DOCUMENT_FILE_URL,
    LOGIN_URL,
    REQUEST_HEADERS,
    SALDO_URL,
)

TIMEOUT = aiohttp.ClientTimeout(total=30)
MAX_DOCUMENT_SIZE = 25 * 1024 * 1024


class Tgc1AuthError(Exception):
    """Authentication failed or the account no longer has access."""


class Tgc1ApiError(Exception):
    """Network problem or unexpected API response."""


class Tgc1RejectedError(Tgc1ApiError):
    """The API rejected a request with a client error."""


class Tgc1Client:
    """Authenticated async client with one retry after an expired token."""

    def __init__(
        self, session: aiohttp.ClientSession, username: str, password: str
    ) -> None:
        self._session = session
        self._username = username
        self._password = password
        self._token: str | None = None
        self._auth_lock = asyncio.Lock()

    async def async_login(self) -> None:
        """Authenticate and cache the short-lived access token."""
        async with self._auth_lock:
            if self._token is not None:
                return

            try:
                async with self._session.post(
                    LOGIN_URL,
                    json={"username": self._username, "password": self._password},
                    headers=REQUEST_HEADERS,
                    timeout=TIMEOUT,
                ) as response:
                    if response.status in (400, 401, 403):
                        raise Tgc1AuthError(
                            f"Login rejected: HTTP {response.status}"
                        )
                    response.raise_for_status()
                    try:
                        data = await response.json(content_type=None)
                    except (aiohttp.ClientError, ValueError) as err:
                        raise Tgc1ApiError(
                            "Login response was not valid JSON"
                        ) from err
            except Tgc1AuthError:
                raise
            except (aiohttp.ClientError, asyncio.TimeoutError) as err:
                raise Tgc1ApiError("Login request failed") from err

            token = data.get("accessToken") if isinstance(data, dict) else None
            if not isinstance(token, str) or not token:
                raise Tgc1AuthError("Login response has no accessToken")
            self._token = token

    async def _async_request(
        self,
        method: str,
        url: str,
        *,
        json_body: Any = None,
        response_format: str = "json",
    ) -> Any:
        """Perform an authenticated request, refreshing the token once."""
        for attempt in range(2):
            if self._token is None:
                await self.async_login()

            headers = {
                **REQUEST_HEADERS,
                "Authorization": f"Bearer {self._token}",
            }
            try:
                async with self._session.request(
                    method,
                    url,
                    headers=headers,
                    json=json_body,
                    timeout=TIMEOUT,
                ) as response:
                    if response.status in (401, 403):
                        self._token = None
                        if attempt == 0:
                            continue
                        raise Tgc1AuthError(
                            f"Access denied: HTTP {response.status}"
                        )

                    if 400 <= response.status < 500:
                        # Do not include response bodies: they may contain personal data.
                        raise Tgc1RejectedError(
                            f"Request rejected: HTTP {response.status}"
                        )

                    response.raise_for_status()

                    if response_format == "empty":
                        return None
                    if response_format == "bytes":
                        return await self._read_document(response)
                    if response_format != "json":
                        raise ValueError(f"Unsupported response format: {response_format}")

                    try:
                        return await response.json(content_type=None)
                    except (aiohttp.ClientError, ValueError) as err:
                        raise Tgc1ApiError(
                            "API returned an invalid JSON response"
                        ) from err
            except (aiohttp.ClientError, asyncio.TimeoutError) as err:
                raise Tgc1ApiError("API request failed") from err

        raise Tgc1ApiError("API request failed after re-authentication")

    @staticmethod
    async def _read_document(response: aiohttp.ClientResponse) -> bytes:
        """Read and validate a bounded PDF response."""
        content_length = response.content_length
        if content_length is not None and content_length > MAX_DOCUMENT_SIZE:
            raise Tgc1ApiError("Document exceeds the 25 MiB limit")

        chunks: list[bytes] = []
        total_size = 0
        async for chunk in response.content.iter_chunked(64 * 1024):
            total_size += len(chunk)
            if total_size > MAX_DOCUMENT_SIZE:
                raise Tgc1ApiError("Document exceeds the 25 MiB limit")
            chunks.append(chunk)

        content = b"".join(chunks)
        if not content.startswith(b"%PDF-"):
            raise Tgc1ApiError("The document response is not a valid PDF")
        return content

    @staticmethod
    def _require_records(data: Any, resource: str) -> list[dict[str, Any]]:
        """Reject schema drift instead of silently reporting empty data."""
        if not isinstance(data, list):
            raise Tgc1ApiError(f"Unexpected {resource} response: expected a list")
        if any(not isinstance(item, dict) for item in data):
            raise Tgc1ApiError(f"Unexpected {resource} response: invalid record")
        return data

    async def async_get_devices(self) -> list[dict[str, Any]]:
        data = await self._async_request("GET", DEVICE_URL)
        records = self._require_records(data, "devices")
        if any("id" not in item for item in records):
            raise Tgc1ApiError("Unexpected devices response: record without id")
        return records

    async def async_get_accounts(self) -> list[dict[str, Any]]:
        data = await self._async_request("GET", SALDO_URL)
        records = self._require_records(data, "accounts")
        if any("id" not in item for item in records):
            raise Tgc1ApiError("Unexpected accounts response: record without id")
        return records

    async def async_get_documents(self) -> list[dict[str, Any]]:
        data = await self._async_request("GET", DOCUMENTS_URL)
        records = self._require_records(data, "documents")
        if any("id" not in item for item in records):
            raise Tgc1ApiError("Unexpected documents response: record without id")
        return records

    async def async_create_reading(self, counter_id: int, value: float) -> None:
        """Submit a meter reading."""
        await self._async_request(
            "POST",
            CREATE_READING_URL,
            json_body={"counterId": counter_id, "value": value},
            response_format="empty",
        )

    async def async_download_document(self, document_id: int) -> bytes:
        """Download a PDF document by its API identifier."""
        return await self._async_request(
            "GET",
            DOCUMENT_FILE_URL.format(id=document_id),
            response_format="bytes",
        )
