"""A single button that connects or disconnects, depending on state."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_MACHINE_NAME, CONF_MODEL, DOMAIN
from .coordinator import SnapmakerCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: SnapmakerCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([SnapmakerConnectionButton(coordinator, entry)])


class SnapmakerConnectionButton(CoordinatorEntity[SnapmakerCoordinator], ButtonEntity):
    """Connects if currently disconnected; disconnects if connected."""

    def __init__(self, coordinator: SnapmakerCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_connection_button"
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
    def name(self) -> str:
        return "Disconnect" if self.coordinator.connected else "Connect"

    @property
    def icon(self) -> str:
        return "mdi:lan-disconnect" if self.coordinator.connected else "mdi:lan-connect"

    @property
    def available(self) -> bool:
        return True

    @property
    def extra_state_attributes(self) -> dict:
        attrs = {"connected": self.coordinator.connected}
        if self.coordinator.last_error:
            attrs["last_error"] = self.coordinator.last_error
        return attrs

    async def async_press(self) -> None:
        if self.coordinator.connected:
            await self.coordinator.async_stop()
        else:
            await self.coordinator.async_start()
