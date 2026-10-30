"""Sensors: meter reading, dates and balance."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import Tgc1ConfigEntry, Tgc1Coordinator
from .entity import device_info as _device_info


def _parse_date(value: Any) -> date | None:
    try:
        return datetime.strptime(str(value), "%d.%m.%Y").date()
    except (TypeError, ValueError):
        return None


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


async def async_setup_entry(
    hass: HomeAssistant,
    entry: Tgc1ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    entities: list[SensorEntity] = []
    
    for dev_id in coordinator.data["devices"]:
        entities.append(Tgc1ReadingSensor(coordinator, dev_id))
        entities.append(Tgc1LastReadingDateSensor(coordinator, dev_id))
        entities.append(Tgc1VerificationDateSensor(coordinator, dev_id))
        
    for acc_id in coordinator.data["accounts"]:
        entities.append(Tgc1DebtSensor(coordinator, acc_id))
        
    # NEW: Добавляем два сенсора для квитанций (привязаны к лицевому счёту)
    entities.append(Tgc1LastReceiptDateSensor(coordinator))
    entities.append(Tgc1LastReceiptIdSensor(coordinator))
    
    async_add_entities(entities)


class Tgc1MeterEntity(CoordinatorEntity[Tgc1Coordinator], SensorEntity):
    _attr_has_entity_name = True

    def __init__(
        self, coordinator: Tgc1Coordinator, dev_id: int, key: str, title: str
    ) -> None:
        super().__init__(coordinator)
        self._dev_id = dev_id
        dev = coordinator.data["devices"][dev_id]
        service = dev.get("serviceName") or "Счётчик"
        self._attr_unique_id = f"{dev_id}_{key}"
        self._attr_name = f"{service} {title}"
        self._attr_device_info = _device_info(
            dev.get("abonentId"), dev.get("personalAccountNumber")
        )

    @property
    def _dev(self) -> dict[str, Any]:
        return self.coordinator.data["devices"].get(self._dev_id, {})

    @property
    def available(self) -> bool:
        return super().available and self._dev_id in self.coordinator.data["devices"]


class Tgc1ReadingSensor(Tgc1MeterEntity):
    """Last submitted meter reading."""

    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    def __init__(self, coordinator: Tgc1Coordinator, dev_id: int) -> None:
        super().__init__(coordinator, dev_id, "reading", "показание")
        service = str(self._dev.get("serviceName", "")).lower().replace("³", "3")
        if service.endswith("м3"):
            self._attr_native_unit_of_measurement = "m³"
            self._attr_device_class = SensorDeviceClass.WATER
        elif "гкал" in service:
            self._attr_native_unit_of_measurement = "Гкал"

    @property
    def native_value(self) -> float | None:
        return _to_float(self._dev.get("lastReading"))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "meter_number": self._dev.get("number"),
            "submission_enabled": self._dev.get("enabled"),
            "verification_required": self._dev.get("requiredVerification"),
            "verification_warning": self._dev.get("verificationWarning"),
        }


class Tgc1LastReadingDateSensor(Tgc1MeterEntity):
    _attr_device_class = SensorDeviceClass.DATE

    def __init__(self, coordinator: Tgc1Coordinator, dev_id: int) -> None:
        super().__init__(coordinator, dev_id, "last_reading_date", "дата последней передачи")

    @property
    def native_value(self) -> date | None:
        return _parse_date(self._dev.get("dtLastReading"))


class Tgc1VerificationDateSensor(Tgc1MeterEntity):
    _attr_device_class = SensorDeviceClass.DATE

    def __init__(self, coordinator: Tgc1Coordinator, dev_id: int) -> None:
        super().__init__(coordinator, dev_id, "next_verification", "следующая поверка")

    @property
    def native_value(self) -> date | None:
        return _parse_date(self._dev.get("dtNextVerification"))


class Tgc1DebtSensor(CoordinatorEntity[Tgc1Coordinator], SensorEntity):
    """Debt on the personal account."""

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_native_unit_of_measurement = "RUB"

    def __init__(self, coordinator: Tgc1Coordinator, acc_id: int) -> None:
        super().__init__(coordinator)
        self._acc_id = acc_id
        self._attr_unique_id = f"{acc_id}_debt"
        self._attr_name = "Задолженность"
        acc = coordinator.data["accounts"][acc_id]
        self._attr_device_info = _device_info(acc_id, acc.get("number"))

    @property
    def _acc(self) -> dict[str, Any]:
        return self.coordinator.data["accounts"].get(self._acc_id, {})

    @property
    def available(self) -> bool:
        return super().available and self._acc_id in self.coordinator.data["accounts"]

    @property
    def native_value(self) -> float | None:
        return _to_float(self._acc.get("debt"))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"debt_with_payment": _to_float(self._acc.get("debtWithPayment"))}


# Базовый класс для сенсоров квитанций, чтобы не дублировать код привязки к устройству
class Tgc1BaseReceiptSensor(CoordinatorEntity[Tgc1Coordinator], SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator: Tgc1Coordinator) -> None:
        super().__init__(coordinator)
        # Привязываем к первому лицевому счёту
        accounts = coordinator.data.get("accounts", {})
        if accounts:
            acc_id = next(iter(accounts))
            acc = accounts[acc_id]
            self._attr_device_info = _device_info(acc_id, acc.get("number"))

    def _latest(self) -> dict[str, Any] | None:
        docs = self.coordinator.data.get("documents", [])
        if not docs:
            return None

        def sort_key(document: dict[str, Any]) -> tuple[datetime, int]:
            try:
                parsed = datetime.strptime(document.get("date", ""), "%d.%m.%Y")
            except (TypeError, ValueError):
                parsed = datetime.min
            try:
                document_id = int(document.get("id", 0))
            except (TypeError, ValueError):
                document_id = 0
            return parsed, document_id

        return max(docs, key=sort_key)


class Tgc1LastReceiptDateSensor(Tgc1BaseReceiptSensor):
    """Дата последней квитанции."""

    _attr_icon = "mdi:calendar-check"
    _attr_name = "Дата последней квитанции"
    _attr_unique_id = "tgc1_last_receipt_date"
    _attr_device_class = SensorDeviceClass.DATE

    @property
    def native_value(self) -> date | None:
        latest = self._latest()
        if not latest:
            return None
        return _parse_date(latest.get("date"))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        latest = self._latest()
        if not latest:
            return {}
        return {
            "period": latest.get("period"),
            "type": latest.get("type"),
            "name": latest.get("name"),
        }


class Tgc1LastReceiptIdSensor(Tgc1BaseReceiptSensor):
    """ID последней квитанции."""

    _attr_icon = "mdi:identifier"
    _attr_name = "ID последней квитанции"
    _attr_unique_id = "tgc1_last_receipt_id"

    @property
    def native_value(self) -> int | None:
        latest = self._latest()
        if not latest:
            return None
        return latest.get("id")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        latest = self._latest()
        if not latest:
            return {}
        return {
            "period": latest.get("period"),
            "date": latest.get("date"),
            "name": latest.get("name"),
        }