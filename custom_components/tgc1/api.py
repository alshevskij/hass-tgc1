"""Small async client for lk.tgc1.ru."""

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


class Tgc1AuthError(Exception):
    """Login was rejected (wrong e-mail/password or access blocked)."""


class Tgc1ApiError(Exception):
    """Network problem or unexpected response."""


class Tgc1RejectedError(Tgc1ApiError):
    """Server answered with a 4xx and (usually) a message."""


class Tgc1Client:
    """Logs in with e-mail + password and keeps a short-lived Bearer token."""

    def __init__(
        self, session: aiohttp.ClientSession, username: str, password: str
    ) -> None:
        self._session = session
        self._username = username
        self._password = password
        self._token: str | None = None

    async def async_login(self) -> None:
        payload = {"username": self._username, "password": self._password}
        try:
            async with self._session.post(
                LOGIN_URL, json=payload, headers=REQUEST_HEADERS, timeout=TIMEOUT
            ) as resp:
                if resp.status in (400, 401, 403):
                    raise Tgc1AuthError(f"Login rejected: HTTP {resp.status}")
                resp.raise_for_status()
                data = await resp.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise Tgc1ApiError(f"Login request failed: {err}") from err

        token = data.get("accessToken") if isinstance(data, dict) else None
        if not token:
            raise Tgc1AuthError("Login response has no accessToken")
        self._token = token

    async def _async_request(
        self,
        method: str,
        url: str,
        *,
        json_body: Any = None,
        expect_json: bool = True,
    ) -> Any:
        for attempt in (1, 2):
            if self._token is None:
                await self.async_login()
            headers = {**REQUEST_HEADERS, "Authorization": f"Bearer {self._token}"}
            try:
                async with self._session.request(
                    method, url, headers=headers, json=json_body, timeout=TIMEOUT
                ) as resp:
                    if resp.status in (401, 403):
                        # Token expired (it lives ~1 hour): log in again once.
                        self._token = None
                        if attempt == 1:
                            continue
                        raise Tgc1AuthError(f"Access denied: HTTP {resp.status}")
                    if 400 <= resp.status < 500:
                        text = (await resp.text())[:300]
                        raise Tgc1RejectedError(f"HTTP {resp.status}: {text}")
                    resp.raise_for_status()
                    if not expect_json:
                        return None
                    return await resp.json(content_type=None)
            except (aiohttp.ClientError, asyncio.TimeoutError) as err:
                raise Tgc1ApiError(f"Request failed: {err}") from err
        raise Tgc1ApiError("Request failed")

    async def async_get_devices(self) -> list[dict[str, Any]]:
        data = await self._async_request("GET", DEVICE_URL)
        return data if isinstance(data, list) else []

    async def async_get_accounts(self) -> list[dict[str, Any]]:
        data = await self._async_request("GET", SALDO_URL)
        return data if isinstance(data, list) else []

    async def async_create_reading(self, counter_id: int, value: float) -> None:
        """Submit a meter reading (same request the website form sends)."""
        await self._async_request(
            "POST",
            CREATE_READING_URL,
            json_body={"counterId": counter_id, "value": value},
            expect_json=False,
        )

    # --- Новые методы для работы с квитанциями ---

    async def async_get_documents(self) -> list[dict[str, Any]]:
        """Получает список документов (квитанций)."""
        data = await self._async_request("GET", DOCUMENTS_URL)
        return data if isinstance(data, list) else []

    async def async_download_document(self, document_id: int) -> bytes:
        """Скачивает PDF квитанции и возвращает байты."""
        url = DOCUMENT_FILE_URL.format(id=document_id)
        
        # Используем _async_request, но просим вернуть не JSON, а сырые байты.
        # Для этого нам придется немного отступить от логики _async_request,
        # так как _async_request всегда возвращает json или None.
        # Проще всего написать отдельный запрос с той же логикой обновления токена.
        
        for attempt in (1, 2):
            if self._token is None:
                await self.async_login()
            headers = {**REQUEST_HEADERS, "Authorization": f"Bearer {self._token}"}
            try:
                async with self._session.request(
                    "GET", url, headers=headers, timeout=TIMEOUT
                ) as resp:
                    if resp.status in (401, 403):
                        self._token = None
                        if attempt == 1:
                            continue
                        raise Tgc1AuthError(f"Access denied: HTTP {resp.status}")
                    if 400 <= resp.status < 500:
                        text = (await resp.text())[:300]
                        raise Tgc1RejectedError(f"HTTP {resp.status}: {text}")
                    resp.raise_for_status()
                    content_type = (
                        resp.headers.get("Content-Type", "").split(";", 1)[0].lower()
                    )
                    content_length = resp.content_length
                    max_size = 25 * 1024 * 1024
                    if content_length is not None and content_length > max_size:
                        raise Tgc1ApiError("PDF exceeds the 25 MiB limit")
                    chunks: list[bytes] = []
                    total_size = 0
                    async for chunk in resp.content.iter_chunked(64 * 1024):
                        total_size += len(chunk)
                        if total_size > max_size:
                            raise Tgc1ApiError("PDF exceeds the 25 MiB limit")
                        chunks.append(chunk)
                    content = b"".join(chunks)
                    if not content.startswith(b"%PDF"):
                        raise Tgc1ApiError(
                            "Unexpected document response "
                            f"(Content-Type: {content_type or 'unknown'})"
                        )
                    return content
            except (aiohttp.ClientError, asyncio.TimeoutError) as err:
                raise Tgc1ApiError(f"Request failed: {err}") from err
        raise Tgc1ApiError("Request failed")
