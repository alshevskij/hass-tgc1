"""Button that sends the typed reading to the personal cabinet."""

from __future__ import annotations

import logging

from homeassistant.components import persistent_notification
from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import Tgc1ConfigEntry, Tgc1Coordinator
from .const import DEFAULT_MAX_INCREASE
from .entity import Tgc1MeterBase, device_info as _device_info

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: Tgc1ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    known_devices = set(coordinator.data["devices"])

    async_add_entities(
        Tgc1SubmitButton(coordinator, dev_id) for dev_id in known_devices
    )

    # Receipt buttons use the shared document feed and are created once.
    receipt_buttons_added = False

    def add_receipt_buttons() -> None:
        nonlocal receipt_buttons_added
        accounts = coordinator.data.get("accounts", {})
        if receipt_buttons_added or not accounts:
            return
        acc_id = next(iter(accounts))
        async_add_entities(
            (
                Tgc1DownloadReceiptButton(coordinator, acc_id),
                Tgc1SaveReceiptButton(coordinator, acc_id),
            )
        )
        receipt_buttons_added = True

    add_receipt_buttons()

    def handle_coordinator_update() -> None:
        new_devices = set(coordinator.data["devices"]) - known_devices
        if new_devices:
            known_devices.update(new_devices)
            async_add_entities(
                Tgc1SubmitButton(coordinator, dev_id) for dev_id in new_devices
            )
        add_receipt_buttons()

    entry.async_on_unload(coordinator.async_add_listener(handle_coordinator_update))


class Tgc1SubmitButton(Tgc1MeterBase, ButtonEntity):
    """Кнопка передачи показаний."""
    _attr_icon = "mdi:send"

    def __init__(self, coordinator: Tgc1Coordinator, dev_id: int) -> None:
        super().__init__(coordinator, dev_id, "reading_submit", "передать показания")

    async def async_press(self) -> None:
        value = self.coordinator.pending_readings.get(self._dev_id)
        if value is None:
            raise ServiceValidationError(
                "Сначала введи показание в поле «… показание к отправке»"
            )
        await self.coordinator.async_submit_reading(
            self._dev_id,
            value,
            DEFAULT_MAX_INCREASE,
            dry_run=False,
            require_increase=True,
        )


class Tgc1DownloadReceiptButton(CoordinatorEntity[Tgc1Coordinator], ButtonEntity):
    """Кнопка для открытия последней квитанции."""

    _attr_has_entity_name = True
    _attr_name = "Открыть последнюю квитанцию"
    _attr_icon = "mdi:file-pdf-box"

    def __init__(self, coordinator: Tgc1Coordinator, acc_id: int) -> None:
        super().__init__(coordinator)
        self._acc_id = acc_id
        self._attr_unique_id = f"{acc_id}_download_receipt"
        
        # Привязываем к тому же устройству, что и остальные кнопки
        acc = coordinator.data["accounts"][acc_id]
        self._attr_device_info = _device_info(acc_id, acc.get("number"))

    async def async_press(self) -> None:
        """Create a notification with a temporary link to the latest receipt."""
        docs = self.coordinator.data.get("documents", [])
        if not docs:
            _LOGGER.warning("Нет доступных квитанций")
            return

        from . import _latest_document

        latest_doc = _latest_document(docs)
        if latest_doc is None:
            return
        document_id = latest_doc.get("id")
        url = self.coordinator.async_document_url(document_id)
        persistent_notification.async_create(
            self.hass,
            f"Квитанция ТГК-1 за {latest_doc.get('period', 'неизвестный период')}. "
            f'<a href="{url}" target="_blank" rel="noopener noreferrer">Открыть PDF</a>',
            title="Квитанция ТГК-1",
            notification_id=f"tgc1_receipt_{self.coordinator._entry_id}",
        )




class Tgc1SaveReceiptButton(CoordinatorEntity[Tgc1Coordinator], ButtonEntity):
    """Download the latest receipt to Home Assistant media storage."""

    _attr_has_entity_name = True
    _attr_name = "Скачать последнюю квитанцию"
    _attr_icon = "mdi:download"

    def __init__(self, coordinator: Tgc1Coordinator, acc_id: int) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{acc_id}_save_receipt"
        acc = coordinator.data["accounts"][acc_id]
        self._attr_device_info = _device_info(acc_id, acc.get("number"))

    async def async_press(self) -> None:
        """Save the latest receipt under /media/tgc1/ on the HA host."""
        from . import _latest_document

        latest_doc = _latest_document(self.coordinator.data.get("documents", []))
        if latest_doc is None:
            persistent_notification.async_create(
                self.hass,
                "В личном кабинете ТГК-1 пока нет доступных квитанций.",
                title="ТГК-1",
                notification_id=f"tgc1_save_receipt_{self.coordinator._entry_id}",
            )
            return

        document_id = int(latest_doc["id"])
        filename = latest_doc.get("name") or f"receipt-{document_id}.pdf"
        path = await self.coordinator.async_download_document(document_id, filename)
        persistent_notification.async_create(
            self.hass,
            f"Квитанция за {latest_doc.get('period', 'неизвестный период')} сохранена: `{path}`",
            title="Квитанция ТГК-1 скачана",
            notification_id=f"tgc1_save_receipt_{self.coordinator._entry_id}",
        )
