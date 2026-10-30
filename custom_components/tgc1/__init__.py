"""ТГК-1: личный кабинет (lk.tgc1.ru) for Home Assistant."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta
import logging
import re
from pathlib import Path
from typing import Any

import voluptuous as vol
from aiohttp import web

from homeassistant.components import persistent_notification
from homeassistant.components.http import KEY_HASS, HomeAssistantView
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
from homeassistant.helpers.storage import Store
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

DOWNLOAD_DOCUMENT_SCHEMA = vol.Schema(
    {
        vol.Required("document_id"): vol.All(vol.Coerce(int), vol.Range(min=1)),
        vol.Optional("config_entry_id"): cv.string,
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
        self._entry_id = entry.entry_id
        self._submit_lock = asyncio.Lock()
        # reading typed in the number entity, per meter id
        self.pending_readings: dict[int, float | None] = {}
        self._document_store = Store(
            hass, 1, f"{DOMAIN}_{entry.entry_id}_seen_documents"
        )
        self._seen_document_ids: set[str] | None = None

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
            "devices": {d["id"]: d for d in devices},
            "accounts": {a["id"]: a for a in accounts},
            "documents": documents,
        }

        current_ids = {str(document["id"]) for document in documents}
        if self._seen_document_ids is None:
            stored = await self._document_store.async_load()
            if isinstance(stored, dict) and isinstance(stored.get("ids"), list):
                self._seen_document_ids = {str(item) for item in stored["ids"]}
            else:
                # First installation: establish a baseline and do not notify for old files.
                self._seen_document_ids = set(current_ids)

        new_documents = [
            document
            for document in documents
            if str(document["id"]) not in self._seen_document_ids
        ]
        for document in sorted(new_documents, key=_document_sort_key):
            document_id = document["id"]
            try:
                url = self.async_document_url(int(document_id))
                message = (
                    "Появилась новая квитанция ТГК-1 за "
                    f"{document.get('period', 'неизвестный период')}. "
                    f'<a href="{url}" target="_blank" rel="noopener noreferrer">Открыть PDF</a>'
                )
                persistent_notification.async_create(
                    self.hass,
                    message,
                    title="Новая квитанция ТГК-1",
                    notification_id=f"tgc1_receipt_{self._entry_id}_{document_id}",
                )
                self.hass.bus.async_fire(
                    "tgc1_new_document",
                    {
                        "document_id": document_id,
                        "name": document.get("name"),
                        "period": document.get("period"),
                        "date": document.get("date"),
                        "url": url,
                    },
                )
                self._seen_document_ids.add(str(document_id))
            except (TypeError, ValueError):
                _LOGGER.warning("Пропускаю документ с некорректным ID: %s", document_id)
                continue
            except Exception:
                _LOGGER.exception(
                    "Не удалось обработать новую квитанцию %s", document_id
                )
                continue

        # Keep storage bounded while retaining enough history to avoid duplicate events.
        try:
            await self._document_store.async_save(
                {"ids": sorted(self._seen_document_ids)[-500:]}
            )
            self._seen_document_ids = set(sorted(self._seen_document_ids)[-500:])
        except OSError:
            _LOGGER.exception("Не удалось сохранить историю квитанций ТГК-1")

        return data

    def async_document_url(self, document_id: int) -> str:
        """Return a 24-hour signed URL for a document in this entry."""
        path = f"/api/tgc1/receipt/{self._entry_id}/{document_id}"
        return async_sign_path(
            self.hass, path, timedelta(hours=24), use_content_user=True
        )

    async def async_download_document(self, document_id: int, filename: str) -> str:
        """Скачивает PDF квитанции в /media/tgc1/ и возвращает путь."""
        try:
            content = await self.client.async_download_document(document_id)
        except Tgc1RejectedError as err:
            raise ServiceValidationError(
                f"ЛК отклонил скачивание документа {document_id}"
            ) from err
        except Tgc1AuthError as err:
            raise HomeAssistantError(
                "Не удалось авторизоваться в личном кабинете ТГК-1"
            ) from err
        except Tgc1ApiError as err:
            raise HomeAssistantError(
                f"Не удалось скачать документ {document_id}"
            ) from err

        safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", Path(filename).name)[:100]
        if not safe_name or safe_name in {".", ".."}:
            safe_name = f"receipt-{document_id}.pdf"
        if not safe_name.lower().endswith(".pdf"):
            safe_name = f"{safe_name}.pdf"
        target = Path(MEDIA_DIR) / f"{Path(safe_name).stem}-{document_id}.pdf"

        def write_file() -> None:
            target.parent.mkdir(parents=True, exist_ok=True)
            # Exclusive creation prevents silently overwriting an earlier receipt.
            with target.open("xb") as pdf_file:
                pdf_file.write(content)

        try:
            await self.hass.async_add_executor_job(write_file)
        except FileExistsError:
            _LOGGER.info("Квитанция %s уже сохранена в %s", document_id, target)
        except OSError as err:
            raise HomeAssistantError(
                f"Не удалось сохранить квитанцию {document_id}"
            ) from err

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

            _LOGGER.info("tgc1 reading accepted by the API: counter_id=%s", dev_id)
            await self.async_refresh()
            refreshed = self.last_update_success
            after = self.data["devices"].get(dev_id, {}) if refreshed else {}
            return {
                **result,
                "sent": True,
                "refreshed": refreshed,
                "reading_in_lk_now": after.get("lastReading"),
                "date_in_lk_now": after.get("dtLastReading"),
            }


class Tgc1ReceiptView(HomeAssistantView):
    """Serve a receipt PDF after validating its config entry and document ID."""

    url = "/api/tgc1/receipt/{entry_id}/{document_id}"
    name = "api:tgc1:receipt"
    requires_auth = True

    async def get(
        self, request: web.Request, entry_id: str, document_id: str
    ) -> web.Response:
        hass = request.app[KEY_HASS]
        entry = hass.config_entries.async_get_entry(entry_id)
        if (
            entry is None
            or entry.domain != DOMAIN
            or entry.state is not ConfigEntryState.LOADED
        ):
            raise web.HTTPNotFound()
        try:
            doc_id = int(document_id)
        except ValueError:
            raise web.HTTPNotFound() from None
        coordinator = entry.runtime_data
        doc = next(
            (
                item
                for item in coordinator.data.get("documents", [])
                if str(item.get("id")) == str(doc_id)
            ),
            None,
        )
        if doc is None:
            raise web.HTTPNotFound()
        try:
            content = await coordinator.client.async_download_document(doc_id)
        except Tgc1RejectedError:
            raise web.HTTPNotFound() from None
        except Tgc1AuthError as err:
            _LOGGER.error(
                "Авторизация ТГК-1 не удалась при выдаче квитанции %s: %s",
                doc_id,
                err,
            )
            raise web.HTTPBadGateway() from err
        except Tgc1ApiError as err:
            _LOGGER.error("Ошибка получения квитанции %s: %s", doc_id, err)
            raise web.HTTPBadGateway() from err
        filename = Path(str(doc.get("name") or f"receipt-{doc_id}.pdf")).name
        filename = re.sub(r"[^A-Za-z0-9._-]", "_", filename)[:120]
        if not filename or filename in {".", ".."}:
            filename = f"receipt-{doc_id}.pdf"
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


def _document_sort_key(document: dict[str, Any]) -> tuple[date, int]:
    """Sort documents by date, then ID, without depending on API ordering."""
    parsed = _parse_date(document.get("date")) or date.min
    try:
        document_id = int(document.get("id", 0))
    except (TypeError, ValueError):
        document_id = 0
    return parsed, document_id


def _latest_document(documents: list[dict[str, Any]]) -> dict[str, Any] | None:
    return max(documents, key=_document_sort_key, default=None)


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
        requested_entry_id = call.data.get("config_entry_id")
        matches = []
        for entry in hass.config_entries.async_entries(DOMAIN):
            if entry.state is not ConfigEntryState.LOADED:
                continue
            if requested_entry_id and entry.entry_id != requested_entry_id:
                continue
            coordinator = entry.runtime_data
            doc = next(
                (
                    item
                    for item in coordinator.data.get("documents", [])
                    if str(item.get("id")) == str(document_id)
                ),
                None,
            )
            if doc is not None:
                matches.append((coordinator, doc))

        if not matches:
            raise ServiceValidationError(
                f"Документ {document_id} не найден в выбранной интеграции ТГК-1"
            )
        if len(matches) > 1:
            raise ServiceValidationError(
                "ID документа найден в нескольких аккаунтах. "
                "Укажи config_entry_id, чтобы выбрать нужный аккаунт."
            )

        coordinator, doc = matches[0]
        path = await coordinator.async_download_document(
            document_id, doc.get("name") or f"{document_id}.pdf"
        )
        return {"path": path}

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
