"""Number entity controlling how often the printer pushes updates."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_MACHINE_NAME,
    CONF_MODEL,
    DOMAIN,
    UPDATE_INTERVAL_MAX_S,
    UPDATE_INTERVAL_MIN_S,
)
from .coordinator import SnapmakerCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: SnapmakerCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([SnapmakerUpdateIntervalNumber(coordinator, entry)])


class SnapmakerUpdateIntervalNumber(CoordinatorEntity[SnapmakerCoordinator], NumberEntity):
    """How often (in seconds) the printer pushes new sensor values.

    Changing this reconnects the SACP session to re-subscribe at the new
    rate -- expect a brief gap in updates when you change it.
    """

    _attr_name = "Update Interval"
    _attr_icon = "mdi:timer-cog-outline"
    _attr_native_min_value = UPDATE_INTERVAL_MIN_S
    _attr_native_max_value = UPDATE_INTERVAL_MAX_S
    _attr_native_step = 1
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS
    _attr_mode = NumberMode.BOX

    def __init__(self, coordinator: SnapmakerCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_update_interval"
        self._entry = entry

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.entry_id)},
            name=self._entry.data.get(CONF_MACHINE_NAME) or self._entry.data[CONF_HOST],
            manufacturer="Snapmaker",
            model=self._entry.data.get(CONF_MODEL) or "Artisan",
            sw_version=self.coordinator.print_head_firmware,
            configuration_url=f"http://{self._entry.data[CONF_HOST]}",
        )

    @property
    def native_value(self) -> float:
        return self.coordinator.update_interval_s

    @property
    def available(self) -> bool:
        return True

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_set_update_interval(int(value))
