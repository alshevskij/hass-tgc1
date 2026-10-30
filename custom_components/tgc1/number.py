"""Input field for the reading that will be sent with the button."""

from __future__ import annotations

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberMode,
    RestoreNumber,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import Tgc1ConfigEntry, Tgc1Coordinator
from .entity import Tgc1MeterBase, meter_unit


async def async_setup_entry(
    hass: HomeAssistant,
    entry: Tgc1ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    known_devices = set(coordinator.data["devices"])

    async_add_entities(
        Tgc1ReadingInput(coordinator, dev_id) for dev_id in known_devices
    )

    def handle_coordinator_update() -> None:
        new_devices = set(coordinator.data["devices"]) - known_devices
        if new_devices:
            known_devices.update(new_devices)
            async_add_entities(
                Tgc1ReadingInput(coordinator, dev_id) for dev_id in new_devices
            )

    entry.async_on_unload(coordinator.async_add_listener(handle_coordinator_update))


class Tgc1ReadingInput(Tgc1MeterBase, RestoreNumber):
    """Reading typed by the user; nothing is sent until the button is pressed."""

    _attr_mode = NumberMode.BOX
    _attr_native_min_value = 0
    _attr_native_max_value = 999999.9999
    _attr_native_step = 0.0001
    _attr_icon = "mdi:counter"

    def __init__(self, coordinator: Tgc1Coordinator, dev_id: int) -> None:
        super().__init__(coordinator, dev_id, "reading_input", "ввод показаний")
        unit = meter_unit(self._dev.get("serviceName"))
        if unit is not None:
            self._attr_native_unit_of_measurement = unit
            if unit == "m³":
                self._attr_device_class = NumberDeviceClass.WATER

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        value = None
        if (last := await self.async_get_last_number_data()) is not None:
            value = last.native_value
        if value is None:
            try:
                value = float(self._dev.get("lastReading"))
            except (TypeError, ValueError):
                value = None
        self.coordinator.pending_readings[self._dev_id] = value

    @property
    def native_value(self) -> float | None:
        return self.coordinator.pending_readings.get(self._dev_id)

    async def async_set_native_value(self, value: float) -> None:
        self.coordinator.pending_readings[self._dev_id] = value
        self.async_write_ha_state()
