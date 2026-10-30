"""ТГК-1: личный кабинет (lk.tgc1.ru) for Home Assistant."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta
from http import HTTPStatus
import logging
import os
from pathlib import Path
from typing import Any

import voluptuous as vol
from aiohttp import web

from homeassistant.components import persistent_notification
from homeassistant.components.http import HomeAssistantView
from homeassistant.components.http.auth import async_sign_path
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    HomeAssistantError,
    ServiceValidationError,
)
from homeassistant.helpers import config_validation as cv, entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import Tgc1ApiError, Tgc1AuthError, Tgc1Client, Tgc1RejectedError
from .const import (
    DEFAULT_MAX_INCREASE,
    DOMAIN,
    MEDIA_DIR,
    SERVICE_DOWNLOAD_DOCUMENT,
    SERVICE_SUBMIT_READING,
    UPDATE_INTERVAL_HOURS,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.NUMBER, Platform.BUTTON]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type Tgc1ConfigEntry = ConfigEntry["Tgc1Coordinator"]

SUBMIT_READING_SCHEMA = vol.Schema(
    {
        vol.Required("entity_id"): cv.entity_id,
        vol.Required("value"): vol.All(
            vol.Coerce(float), vol.Range(min=0, max=999999.9999)
        ),
        vol.Optional("max_increase", default=DEFAULT_MAX_INCREASE): vol.All(
            vol.Coerce(float), vol.Range(min=0)
        ),
        vol.Optional("dry_run", default=False): cv.boolean,
    }
)

# NEW: Схема для сервиса скачивания
DOWNLOAD_DOCUMENT_SCHEMA = vol.Schema(
    {
        vol.Required("document_id"): vol.Coerce(int),
    }
)


def _parse_date(value: Any) -> date | None:
    try:
        return datetime.strptime(str(value), "%d.%m.%Y").date()
    except (TypeError, ValueError):
        return None


class Tgc1Coordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Fetches meters and balance in one go."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, client: Tgc1Client
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            config_entry=entry,
            update_interval=timedelta(hours=UPDATE_INTERVAL_HOURS),
        )
        self.client = client
        self._submit_lock = asyncio.Lock()
        # reading typed in the number entity, per meter id
        self.pending_readings: dict[int, float | None] = {}
        # NEW: храним ID последнего известного документа
        self._last_document_id: int | None = None

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            devices = await self.client.async_get_devices()
            accounts = await self.client.async_get_accounts()
            # NEW: получаем документы
            documents = await self.client.async_get_documents()
        except Tgc1AuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except Tgc1ApiError as err:
            raise UpdateFailed(str(err)) from err

        data = {
            "devices": {d["id"]: d for d in devices if "id" in d},
            "accounts": {a["id"]: a for a in accounts if "id" in a},
            "documents": documents,  # NEW: список документов
        }

        newest = _latest_document(documents)
        if newest is not None:
            newest_id = newest.get("id")
            if self._last_document_id is not None and newest_id != self._last_document_id:
                try:
                    url = self.async_document_url(newest_id)
                    message = (
                        f"Появилась новая квитанция ТГК-1 за {newest.get('period', 'неизвестный период')}. "
                        f"[Открыть PDF]({url})"
                    )
                    persistent_notification.async_create(
                        self.hass,
                        message,
                        title="Новая квитанция ТГК-1",
                        notification_id=f"tgc1_receipt_{self.config_entry.entry_id}",
                    )
                    event_data = {
                        "document_id": newest_id,
                        "name": newest.get("name"),
                        "period": newest.get("period"),
                        "date": newest.get("date"),
                        "url": url,
                    }
                    self.hass.bus.async_fire("tgc1_new_document", event_data)
                except Exception:
                    _LOGGER.exception("Не удалось создать уведомление о квитанции %s", newest_id)
            self._last_document_id = newest_id

        return data

    def async_document_url(self, document_id: int) -> str:
        """Return a 24-hour signed URL for a document in this entry."""
        path = f"/api/tgc1/receipt/{self.config_entry.entry_id}/{document_id}"
        return async_sign_path(
            self.hass, path, timedelta(hours=24), use_content_user=True
        )

    async def async_download_document(self, document_id: int, filename: str) -> str:
        """Скачивает PDF квитанции в /media/tgc1/ и возвращает путь."""
        os.makedirs(MEDIA_DIR, exist_ok=True)
        safe_name = Path(filename).name
        target = Path(MEDIA_DIR) / safe_name

        try:
            content = await self.client.async_download_document(document_id)
        except Tgc1RejectedError as err:
            raise ServiceValidationError(f"ЛК отклонил скачивание: {err}") from err
        except Tgc1ApiError as err:
            raise HomeAssistantError(f"Ошибка сети при скачивании: {err}") from err

        if not content.startswith(b"%PDF"):
            raise HomeAssistantError(f"Документ {document_id} не является PDF")

        with open(target, "wb") as f:
            f.write(content)

        _LOGGER.info("Квитанция %s сохранена в %s", document_id, target)
        return str(target)

    async def async_submit_reading(
        self,
        dev_id: int,
        value: float,
        max_increase: float,
        dry_run: bool,
        require_increase: bool = False,
    ) -> dict[str, Any]:
        """Validate and (unless dry_run) send a reading to the personal cabinet."""
        async with self._submit_lock:
            # Always validate against fresh data, not what was polled hours ago.
            await self.async_refresh()
            if not self.last_update_success:
                raise HomeAssistantError(
                    "Не удалось получить свежие данные из ЛК, отправка отменена"
                )

            dev = self.data["devices"].get(dev_id)
            if dev is None:
                raise ServiceValidationError("Счётчик не найден в ЛК")
            if not dev.get("enabled"):
                raise ServiceValidationError(
                    "ЛК сейчас не принимает показания по этому счётчику"
                )
            try:
                last = float(dev.get("lastReading"))
            except (TypeError, ValueError) as err:
                raise ServiceValidationError(
                    "Не удалось определить предыдущее показание"
                ) from err

            value = round(value, 4)
            if value < last:
                raise ServiceValidationError(
                    f"Новое показание {value} меньше предыдущего {last}"
                )
            if require_increase and value <= last:
                raise ServiceValidationError(
                    f"Введи новое показание: оно должно быть больше предыдущего ({last})"
                )
            if value - last > max_increase:
                raise ServiceValidationError(
                    f"Разница с предыдущим показанием ({round(value - last, 4)}) "
                    f"больше допустимой ({max_increase}). Проверь значение или "
                    "увеличь max_increase"
                )
            if value == last and _parse_date(dev.get("dtLastReading")) == date.today():
                raise ServiceValidationError(
                    "Такое же показание уже передано сегодня"
                )

            result: dict[str, Any] = {
                "counter_id": dev_id,
                "previous_reading": last,
                "new_reading": value,
                "difference": round(value - last, 4),
            }
            if dry_run:
                _LOGGER.info("tgc1 dry run, nothing sent: %s", result)
                return {**result, "sent": False}

            try:
                await self.client.async_create_reading(dev_id, value)
            except Tgc1RejectedError as err:
                raise ServiceValidationError(f"ЛК отклонил показание: {err}") from err
            except Tgc1AuthError as err:
                raise HomeAssistantError(
                    f"Не удалось авторизоваться в ЛК, показание не отправлено: {err}"
                ) from err
            except Tgc1ApiError as err:
                raise HomeAssistantError(
                    "Не удалось подтвердить отправку, проверь показания в ЛК "
                    f"перед повторной попыткой: {err}"
                ) from err

            _LOGGER.info("tgc1 reading sent: %s", result)
            await self.async_refresh()
            after = self.data["devices"].get(dev_id, {})
            return {
                **result,
                "sent": True,
                "reading_in_lk_now": after.get("lastReading"),
                "date_in_lk_now": after.get("dtLastReading"),
            }


class Tgc1ReceiptView(HomeAssistantView):
    """Serve a receipt PDF after validating its config entry and document ID."""

    url = "/api/tgc1/receipt/{entry_id}/{document_id}"
    name = "api:tgc1:receipt"
    requires_auth = True

    async def get(self, request: web.Request, entry_id: str, document_id: str) -> web.Response:
        hass = request.app["hass"]
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry is None or entry.domain != DOMAIN or entry.state is not ConfigEntryState.LOADED:
            raise web.HTTPNotFound()
        try:
            doc_id = int(document_id)
        except ValueError:
            raise web.HTTPNotFound() from None
        coordinator = entry.runtime_data
        doc = next((item for item in coordinator.data.get("documents", []) if item.get("id") == doc_id), None)
        if doc is None:
            raise web.HTTPNotFound()
        try:
            content = await coordinator.client.async_download_document(doc_id)
        except Tgc1RejectedError:
            raise web.HTTPNotFound() from None
        except Tgc1ApiError as err:
            _LOGGER.error("Ошибка получения квитанции %s: %s", doc_id, err)
            raise web.HTTPBadGateway() from err
        filename = Path(str(doc.get("name") or f"receipt-{doc_id}.pdf")).name
        if not filename.lower().endswith(".pdf"):
            filename = f"{filename}.pdf"
        return web.Response(
            body=content,
            content_type="application/pdf",
            headers={
                "Content-Disposition": f'inline; filename="{filename}"',
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )


def _latest_document(documents: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Select the newest document independently of API ordering."""
    def sort_key(document: dict[str, Any]) -> tuple[date, int]:
        parsed = _parse_date(document.get("date")) or date.min
        try:
            doc_id = int(document.get("id", 0))
        except (TypeError, ValueError):
            doc_id = 0
        return parsed, doc_id

    return max(documents, key=sort_key, default=None)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    hass.http.register_view(Tgc1ReceiptView())

    async def _handle_submit_reading(call: ServiceCall) -> ServiceResponse:
        registry = er.async_get(hass)
        ent = registry.async_get(call.data["entity_id"])
        if (
            ent is None
            or ent.platform != DOMAIN
            or not ent.unique_id.endswith("_reading")
        ):
            raise ServiceValidationError(
                "Выбери сенсор «… показание» от интеграции ТГК-1"
            )
        entry = hass.config_entries.async_get_entry(ent.config_entry_id)
        if entry is None or entry.state is not ConfigEntryState.LOADED:
            raise ServiceValidationError("Интеграция ТГК-1 сейчас не загружена")
        try:
            dev_id = int(ent.unique_id.removesuffix("_reading"))
        except ValueError as err:
            raise ServiceValidationError("Не удалось определить счётчик") from err

        return await entry.runtime_data.async_submit_reading(
            dev_id,
            call.data["value"],
            call.data["max_increase"],
            call.data["dry_run"],
        )

    # NEW: обработчик скачивания документа
    async def _handle_download_document(call: ServiceCall) -> ServiceResponse:
        document_id = call.data["document_id"]
        # Ищем координатор в загруженных конфигурациях
        for entry in hass.config_entries.async_entries(DOMAIN):
            if entry.state is not ConfigEntryState.LOADED:
                continue
            coordinator = entry.runtime_data
            docs = coordinator.data.get("documents", [])
            doc = next((d for d in docs if d.get("id") == document_id), None)
            if doc is None:
                continue
            path = await coordinator.async_download_document(document_id, doc.get("name") or f"{document_id}.pdf")
            return {"path": path}
        raise ServiceValidationError(f"Документ {document_id} не найден в загруженных аккаунтах ТГК-1")

    hass.services.async_register(
        DOMAIN,
        SERVICE_SUBMIT_READING,
        _handle_submit_reading,
        schema=SUBMIT_READING_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )

    # NEW: Регистрация сервиса скачивания
    hass.services.async_register(
        DOMAIN,
        SERVICE_DOWNLOAD_DOCUMENT,
        _handle_download_document,
        schema=DOWNLOAD_DOCUMENT_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )

    return True


async def async_setup_entry(hass: HomeAssistant, entry: Tgc1ConfigEntry) -> bool:
    client = Tgc1Client(
        async_get_clientsession(hass),
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
    )
    coordinator = Tgc1Coordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: Tgc1ConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)