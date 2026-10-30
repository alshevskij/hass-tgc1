"""Shared helpers and base class for TGC-1 entities."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN

if TYPE_CHECKING:
    from . import Tgc1Coordinator


def device_info(abonent_id: Any, account_number: Any) -> DeviceInfo:
    tail = str(account_number)[-4:] if account_number else str(abonent_id)
    return DeviceInfo(
        identifiers={(DOMAIN, str(abonent_id))},
        name=f"ТГК-1 {tail}",
        manufacturer="ПАО «ТГК-1»",
        model="Личный кабинет",
    )


def meter_display_name(service_name: Any, title: str) -> str:
    """Build a concise entity name, with a stable GVS prefix."""
    service = str(service_name or "Счётчик").strip()
    normalized = service.lower().replace("ё", "е")
    if "гвс" in normalized or "горяч" in normalized:
        return f"ГВС: {title}"
    return f"{service} {title}"


def meter_unit(service_name: Any) -> str | None:
    """Unit guessed from the service name, e.g. 'ГВС м3' -> 'm³'."""
    service = str(service_name or "").lower().replace("³", "3")
    if service.endswith("м3"):
        return "m³"
    if "гкал" in service:
        return "Гкал"
    return None


class Tgc1MeterBase(CoordinatorEntity["Tgc1Coordinator"]):
    """Base for per-meter entities (number, button)."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: "Tgc1Coordinator", dev_id: int, key: str, title: str
    ) -> None:
        super().__init__(coordinator)
        self._dev_id = dev_id
        dev = coordinator.data["devices"][dev_id]
        service = dev.get("serviceName") or "Счётчик"
        self._attr_unique_id = f"{dev_id}_{key}"
        self._attr_name = meter_display_name(service, title)
        self._attr_device_info = device_info(
            dev.get("abonentId"), dev.get("personalAccountNumber")
        )

    @property
    def _dev(self) -> dict[str, Any]:
        return self.coordinator.data["devices"].get(self._dev_id, {})

    @property
    def available(self) -> bool:
        return super().available and self._dev_id in self.coordinator.data["devices"]
