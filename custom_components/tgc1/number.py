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
from .entity import Tgc1MeterBase, meter_unit, device_info as _device_info


async def async_setup_entry(
    hass: HomeAssistant,
    entry: Tgc1ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    known_devices = set(coordinator.data["devices"])
    known_accounts = set(coordinator.data["accounts"])

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

    async_add_entities(
        Tgc1PaymentAmountInput(coordinator, acc_id) for acc_id in known_accounts
    )

    previous_handle_coordinator_update = handle_coordinator_update

    def handle_coordinator_update() -> None:
        previous_handle_coordinator_update()
        new_accounts = set(coordinator.data["accounts"]) - known_accounts
        if new_accounts:
            known_accounts.update(new_accounts)
            async_add_entities(
                Tgc1PaymentAmountInput(coordinator, acc_id)
                for acc_id in new_accounts
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



class Tgc1PaymentAmountInput(RestoreNumber):
    """Amount to use when creating an SBP payment for this account."""

    _attr_has_entity_name = True
    _attr_name = "Сумма платежа"
    _attr_icon = "mdi:cash-plus"
    _attr_device_class = NumberDeviceClass.MONETARY
    _attr_native_unit_of_measurement = "RUB"
    _attr_mode = NumberMode.BOX
    _attr_native_min_value = 0.01
    _attr_native_max_value = 1000000
    _attr_native_step = 0.01

    def __init__(self, coordinator: Tgc1Coordinator, acc_id: int) -> None:
        super().__init__()
        self.coordinator = coordinator
        self._acc_id = acc_id
        self._attr_unique_id = f"{acc_id}_payment_amount_input"
        account = coordinator.data["accounts"][acc_id]
        self._attr_device_info = _device_info(acc_id, account.get("number"))
        self._value: float | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        restored = await self.async_get_last_number_data()
        if restored is not None and restored.native_value is not None:
            self._value = restored.native_value
        else:
            account = self.coordinator.data["accounts"].get(self._acc_id, {})
            try:
                self._value = max(
                    0.01,
                    float(account.get("debtWithPayment") or account.get("debt") or 0),
                )
            except (TypeError, ValueError):
                self._value = 0.01
        self.async_write_ha_state()

    @property
    def native_value(self) -> float | None:
        return self._value

    async def async_set_native_value(self, value: float) -> None:
        self._value = round(value, 2)
        self.async_write_ha_state()
