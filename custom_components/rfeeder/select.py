"""Select entities for the RFeeder integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import proto
from .const import DOMAIN
from .coordinator import RFeederCoordinator
from .entity import RFeederEntity

# Cooling levels exactly like the R Feeder app (off / weak / medium / strong);
# the device stores the target in half-degrees Celsius.
COOLING_LEVELS = ["off", "weak", "medium", "strong"]
_COOLING_LEVEL_WIRE = {"weak": 20, "medium": 17, "strong": 14}


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: RFeederCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[SelectEntity] = []
    for device_id in coordinator.data.get("devices", {}):
        entities.extend(
            [
                RFeederCompartmentSelect(coordinator, device_id),
                RFeederCoolingLevelSelect(coordinator, device_id),
            ]
        )
    async_add_entities(entities)


class RFeederCompartmentSelect(RFeederEntity, SelectEntity):
    """Tray compartment to feed from on the next feed-now action.

    The compartment is a per-call service argument (not a device setting), so
    the selection is kept in the coordinator and used by the feed-now button
    and the rfeeder.feed_now service.
    """

    _attr_translation_key = "feed_compartment"
    _attr_icon = "mdi:view-grid-plus-outline"

    def __init__(self, coordinator: RFeederCoordinator, device_id: str) -> None:
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_feed_compartment"

    def _compartment_count(self) -> int:
        value = self.shadow_value("10:11:5")
        try:
            return max(1, int(value))
        except (TypeError, ValueError):
            return 4

    @property
    def options(self) -> list[str]:
        return [str(i) for i in range(1, self._compartment_count() + 1)]

    @property
    def current_option(self) -> str | None:
        default = self.shadow_value("10:11:6")
        try:
            default_index = int(default)
        except (TypeError, ValueError):
            default_index = 1
        return str(self.coordinator.get_compartment(self._device_id, default_index))

    async def async_select_option(self, option: str) -> None:
        self.coordinator.set_compartment(self._device_id, int(option))
        self.async_write_ha_state()


class RFeederCoolingLevelSelect(RFeederEntity, SelectEntity):
    """Cooling level (off / weak / medium / strong), same choices as the app.

    Maps to temperatureControlOn (off) and expectedCoolingTemperature
    (weak=10°C, medium=8.5°C, strong=7°C) on the device.
    """

    _attr_translation_key = "cooling_level"
    _attr_icon = "mdi:snowflake"

    def __init__(self, coordinator: RFeederCoordinator, device_id: str) -> None:
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_cooling_level"

    @property
    def options(self) -> list[str]:
        return COOLING_LEVELS

    @property
    def current_option(self) -> str | None:
        control_on = self.shadow_value("10:11:10")
        if control_on is None:
            return None
        if not control_on:
            return "off"
        target = self.shadow_value("10:11:12")  # already normalized to °C
        try:
            target = float(target)
        except (TypeError, ValueError):
            return None
        # weak=10, medium=8.5, strong=7 -> nearest level
        levels = {"weak": 10.0, "medium": 8.5, "strong": 7.0}
        return min(levels, key=lambda level: abs(levels[level] - target))

    async def async_select_option(self, option: str) -> None:
        if option == "off":
            await self.coordinator.async_write_attributes(
                self._device_id,
                proto.CLUSTER_FEED,
                {proto.ATTR_TEMPERATURE_CONTROL_ON: proto.encode_primitive_data(False)},
            )
            return
        # The device firmware only applies ONE attribute per WriteRequest
        # (the app sends separate publishes as well).
        await self.coordinator.async_write_attributes(
            self._device_id,
            proto.CLUSTER_FEED,
            {proto.ATTR_TEMPERATURE_CONTROL_ON: proto.encode_primitive_data(True)},
        )
        await self.coordinator.async_write_attributes(
            self._device_id,
            proto.CLUSTER_FEED,
            {
                proto.ATTR_EXPECTED_COOLING_TEMPERATURE: proto.encode_primitive_data(
                    _COOLING_LEVEL_WIRE[option]
                )
            },
        )
