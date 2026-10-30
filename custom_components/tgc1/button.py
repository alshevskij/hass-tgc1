"""Button that sends the typed reading to the personal cabinet."""

from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import Tgc1ConfigEntry, Tgc1Coordinator
from .const import DEFAULT_MAX_INCREASE
from .entity import Tgc1MeterBase, device_info as _device_info
from homeassistant.helpers.update_coordinator import CoordinatorEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: Tgc1ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    entities = []
    
    # 1. Существующие кнопки передачи показаний
    for dev_id in coordinator.data["devices"]:
        entities.append(Tgc1SubmitButton(coordinator, dev_id))
        
    # 2. Новая кнопка для скачивания последней квитанции
    # Привязываем к первому лицевому счету
    if coordinator.data.get("accounts"):
        acc_id = next(iter(coordinator.data["accounts"]))
        entities.append(Tgc1DownloadReceiptButton(coordinator, acc_id))

    async_add_entities(entities)


class Tgc1SubmitButton(Tgc1MeterBase, ButtonEntity):
    """Кнопка передачи показаний."""
    _attr_icon = "mdi:send"

    def __init__(self, coordinator: Tgc1Coordinator, dev_id: int) -> None:
        super().__init__(coordinator, dev_id, "reading_submit", "передать показание")

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
    """Кнопка для скачивания последней квитанции."""

    _attr_has_entity_name = True
    _attr_name = "Скачать последнюю квитанцию"
    _attr_icon = "mdi:file-download-outline"

    def __init__(self, coordinator: Tgc1Coordinator, acc_id: int) -> None:
        super().__init__(coordinator)
        self._acc_id = acc_id
        self._attr_unique_id = f"{acc_id}_download_receipt"
        
        # Привязываем к тому же устройству, что и остальные кнопки
        acc = coordinator.data["accounts"][acc_id]
        self._attr_device_info = _device_info(acc_id, acc.get("number"))

    async def async_press(self) -> None:
        """Вызывается при нажатии кнопки."""
        docs = self.coordinator.data.get("documents", [])
        if not docs:
            _LOGGER.error("Нет доступных документов для скачивания")
            return

        # Берём самый свежий (API отдаёт по убыванию даты)
        latest_doc = docs[0]
        doc_id = latest_doc["id"]
        filename = latest_doc["name"]

        _LOGGER.info("Скачиваем последнюю квитанцию: %s (%s)", filename, doc_id)

        try:
            # Вызываем метод координатора для скачивания
            path = await self.coordinator.async_download_document(doc_id, filename)
            _LOGGER.info("Квитанция успешно сохранена: %s", path)
            
            # Опционально: можно выстрелить событием, чтобы автоматизация отправила уведомление
            self.hass.bus.async_fire(
                "tgc1_new_document",
                {
                    "document_id": doc_id,
                    "name": filename,
                    "period": latest_doc["period"],
                    "date": latest_doc["date"],
                    "path": path,
                },
            )
        except Exception as err:
            _LOGGER.error("Ошибка при скачивании квитанции: %s", err)
            raise ServiceValidationError(f"Не удалось скачать квитанцию: {err}")