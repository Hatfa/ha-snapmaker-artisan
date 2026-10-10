"""Sensor platform for Snapmaker Artisan.

All sensors are enabled by default. Position/Homed now work (direct
query, confirmed), as does Enclosure (confirmed byte mapping). Progress/
Remaining Time/Total Print Lines still won't -- that query isn't
acknowledged at all on this printer, unrelated to this update.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, PERCENTAGE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_MACHINE_NAME, CONF_MODEL, DOMAIN
from .coordinator import SnapmakerCoordinator


@dataclass(frozen=True, kw_only=True)
class SnapmakerSensorDescription(SensorEntityDescription):
    """Adds the bits needed to pull a value out of coordinator.data."""

    subscription_key: str = ""
    value_fn: Callable[[dict[str, Any]], Any] = lambda data: None


def _pos(axis: str) -> Callable[[dict[str, Any]], Any]:
    def _fn(data):
        position = data.get("position")
        return position.get(axis) if position else None
    return _fn


def _fan(index: int) -> Callable[[dict[str, Any]], Any]:
    def _fn(data):
        fans = data.get("fan")
        if not fans:
            return None
        for f in fans:
            if f["id"] == index:
                return f["percent"]
        return None
    return _fn


def _extruder(tool: int, field: str) -> Callable[[dict[str, Any]], Any]:
    def _fn(data):
        tools = data.get("extruders")
        if not tools:
            return None
        for t in tools:
            if t["tool"] == tool:
                return t[field]
        return None
    return _fn


def _bed(zone: int, field: str) -> Callable[[dict[str, Any]], Any]:
    def _fn(data):
        zones = data.get("bed")
        if not zones:
            return None
        for z in zones:
            if z["zone"] == zone:
                return z[field]
        return None
    return _fn


def _enclosure(field: str) -> Callable[[dict[str, Any]], Any]:
    def _fn(data):
        enclosure = data.get("enclosure")
        return enclosure.get(field) if enclosure else None
    return _fn


def _remaining_seconds(data: dict[str, Any]) -> Any:
    total = data.get("total_lines")
    line = data.get("print_line")
    elapsed = data.get("elapsed_s")
    if not total or not line or elapsed is None or line <= 0 or total <= line:
        return None
    return round(elapsed * (total - line) / line)


def _progress_percent(data: dict[str, Any]) -> Any:
    total = data.get("total_lines")
    line = data.get("print_line")
    if not total or line is None or total <= 0:
        return None
    return round(min(100.0, line / total * 100), 1)


def _format_hms(seconds: Any) -> Any:
    """Formats a second count as H:MM:SS (hours unpadded, no leading zero)."""
    if seconds is None:
        return None
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:d}:{m:02d}:{s:02d}"


SENSOR_DESCRIPTIONS: tuple[SnapmakerSensorDescription, ...] = (
    SnapmakerSensorDescription(
        key="status",
        name="Status",
        icon="mdi:printer-3d",
        subscription_key="status",
        value_fn=lambda data: data.get("status"),
        entity_registry_enabled_default=True,
    ),
    SnapmakerSensorDescription(
        key="position_x",
        name="Position X",
        icon="mdi:axis-x-arrow",
        subscription_key="position",
        native_unit_of_measurement="mm",
        value_fn=_pos("x"),
        entity_registry_enabled_default=True,
    ),
    SnapmakerSensorDescription(
        key="position_y",
        name="Position Y",
        icon="mdi:axis-y-arrow",
        subscription_key="position",
        native_unit_of_measurement="mm",
        value_fn=_pos("y"),
        entity_registry_enabled_default=True,
    ),
    SnapmakerSensorDescription(
        key="position_z",
        name="Position Z",
        icon="mdi:axis-z-arrow",
        subscription_key="position",
        native_unit_of_measurement="mm",
        value_fn=_pos("z"),
        entity_registry_enabled_default=True,
    ),
    SnapmakerSensorDescription(
        key="homed",
        name="Homed",
        icon="mdi:home-import-outline",
        subscription_key="position",
        value_fn=lambda data: (data.get("position") or {}).get("homed"),
        entity_registry_enabled_default=True,
    ),
    SnapmakerSensorDescription(
        key="elapsed_time",
        name="Print Elapsed Time",
        icon="mdi:timer-outline",
        subscription_key="elapsed_s",
        value_fn=lambda data: _format_hms(data.get("elapsed_s")),
        entity_registry_enabled_default=True,
    ),
    SnapmakerSensorDescription(
        key="print_line",
        name="Current Print Line",
        icon="mdi:file-code-outline",
        subscription_key="print_line",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: data.get("print_line"),
        entity_registry_enabled_default=True,
    ),
    # Total Print Lines / Progress / Remaining Time: the printer never
    # acknowledges this query at all on this hardware. Kept in case a
    # future firmware/model supports it, but expect "unknown" always.
    SnapmakerSensorDescription(
        key="total_lines",
        name="Total Print Lines",
        icon="mdi:file-code-outline",
        subscription_key="total_lines",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: data.get("total_lines"),
        entity_registry_enabled_default=True,
    ),
    SnapmakerSensorDescription(
        key="progress",
        name="Print Progress",
        icon="mdi:progress-clock",
        subscription_key="total_lines",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_progress_percent,
        entity_registry_enabled_default=True,
    ),
    SnapmakerSensorDescription(
        key="remaining_time",
        name="Print Remaining Time",
        icon="mdi:timer-sand",
        subscription_key="total_lines",
        value_fn=lambda data: _format_hms(_remaining_seconds(data)),
        entity_registry_enabled_default=True,
    ),
    *(
        SnapmakerSensorDescription(
            key=f"fan_{i}",
            name=f"Fan {i}",
            icon="mdi:fan",
            subscription_key="fan",
            native_unit_of_measurement=PERCENTAGE,
            state_class=SensorStateClass.MEASUREMENT,
            value_fn=_fan(i),
            entity_registry_enabled_default=True,
        )
        for i in range(3)
    ),
    *(
        SnapmakerSensorDescription(
            key=f"extruder_{t}_{field}",
            name=f"Extruder {t} {'Current' if field == 'current' else 'Target'} Temperature",
            icon="mdi:printer-3d-nozzle-heat",
            subscription_key="extruders",
            device_class=SensorDeviceClass.TEMPERATURE,
            native_unit_of_measurement=UnitOfTemperature.CELSIUS,
            state_class=SensorStateClass.MEASUREMENT,
            value_fn=_extruder(t, field),
            entity_registry_enabled_default=True,
        )
        for t in range(2)
        for field in ("current", "target")
    ),
    *(
        SnapmakerSensorDescription(
            key=f"bed_{field}",
            name=f"Bed {'Current' if field == 'current' else 'Target'} Temperature",
            icon="mdi:radiator",
            subscription_key="bed",
            device_class=SensorDeviceClass.TEMPERATURE,
            native_unit_of_measurement=UnitOfTemperature.CELSIUS,
            state_class=SensorStateClass.MEASUREMENT,
            value_fn=_bed(0, field),
            entity_registry_enabled_default=True,
        )
        for field in ("current", "target")
    ),
    # Enclosure -- confirmed byte mapping, 2026-10-04.
    SnapmakerSensorDescription(
        key="enclosure_door",
        name="Enclosure Door",
        icon="mdi:door",
        subscription_key="enclosure",
        value_fn=lambda data: (
            {True: "open", False: "closed"}.get(_enclosure("door_open")(data))
        ),
        entity_registry_enabled_default=True,
    ),
    SnapmakerSensorDescription(
        key="enclosure_fan",
        name="Enclosure Fan",
        icon="mdi:fan",
        subscription_key="enclosure",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_enclosure("fan_percent"),
        entity_registry_enabled_default=True,
    ),
    SnapmakerSensorDescription(
        key="enclosure_door_detect_3dp",
        name="Enclosure Door Detection (3D Print)",
        icon="mdi:door-sliding-lock",
        subscription_key="enclosure",
        value_fn=lambda data: (
            {True: "on", False: "off"}.get(_enclosure("door_detect_3dp")(data))
        ),
        entity_registry_enabled_default=True,
    ),
    SnapmakerSensorDescription(
        key="enclosure_door_detect_cnc",
        name="Enclosure Door Detection (CNC)",
        icon="mdi:door-sliding-lock",
        subscription_key="enclosure",
        value_fn=lambda data: (
            {True: "on", False: "off"}.get(_enclosure("door_detect_cnc")(data))
        ),
        entity_registry_enabled_default=True,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: SnapmakerCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        SnapmakerSensor(coordinator, entry, description)
        for description in SENSOR_DESCRIPTIONS
    )


class SnapmakerSensor(CoordinatorEntity[SnapmakerCoordinator], SensorEntity):
    """A single Snapmaker Artisan value."""

    entity_description: SnapmakerSensorDescription

    def __init__(
        self,
        coordinator: SnapmakerCoordinator,
        entry: ConfigEntry,
        description: SnapmakerSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._entry = entry

    @property
    def device_info(self) -> DeviceInfo:
        # sw_version is populated once the device-info query succeeds
        # (print head firmware, e.g. "v1.14.6") -- falls back to None
        # (hidden in the UI) until then.
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.entry_id)},
            name=self._entry.data.get(CONF_MACHINE_NAME) or self._entry.data[CONF_HOST],
            manufacturer="Snapmaker",
            model=self._entry.data.get(CONF_MODEL) or "Artisan",
            sw_version=self.coordinator.print_head_firmware,
            configuration_url=f"http://{self._entry.data[CONF_HOST]}",
        )

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def available(self) -> bool:
        return (
            self.coordinator.connected
            and self.entity_description.subscription_key in self.coordinator.available_keys
        )
