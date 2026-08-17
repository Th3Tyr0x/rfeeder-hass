"""Number entities for writable numeric settings of the RFeeder."""

from __future__ import annotations

from typing import Any

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import proto
from .const import DOMAIN
from .coordinator import RFeederCoordinator
from .entity import RFeederEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: RFeederCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[NumberEntity] = []
    for device_id in coordinator.data.get("devices", {}):
        entities.extend(
            [
                RFeederFeedDurationNumber(coordinator, device_id),
                RFeederHeatingTemperatureNumber(coordinator, device_id),
            ]
        )
    async_add_entities(entities)


class RFeederFeedDurationNumber(RFeederEntity, NumberEntity):
    """Lid opening duration per feeding, in minutes (like the app)."""

    _attr_translation_key = "feed_duration"
    _attr_icon = "mdi:timer-outline"
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_native_min_value = 1
    _attr_native_max_value = 60
    _attr_native_step = 1
    _attr_mode = NumberMode.BOX

    def __init__(self, coordinator: RFeederCoordinator, device_id: str) -> None:
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_feed_duration"

    def _preferences(self) -> dict[str, Any]:
        value = self.shadow_value("10:11:7")
        return value if isinstance(value, dict) else {}

    @property
    def native_value(self) -> float | None:
        seconds = self._preferences().get("feedDurationSeconds")
        try:
            return round(float(seconds) / 60)
        except (TypeError, ValueError):
            return None

    async def async_set_native_value(self, value: float) -> None:
        prefs = self._preferences()
        await self.coordinator.async_write_attributes(
            self._device_id,
            proto.CLUSTER_FEED,
            {
                proto.ATTR_PREFERENCES: proto.encode_feed_preferences(
                    feed_duration_seconds=int(value * 60),
                    heat_before_feeding=bool(prefs.get("heatBeforeFeeding", False)),
                    expected_heating_temperature=float(
                        prefs.get("expectedHeatingTemperature", 24)
                    ),
                )
            },
        )


class RFeederHeatingTemperatureNumber(RFeederEntity, NumberEntity):
    """Target temperature for the pre-heating function (heatBeforeFeeding).

    Not exposed in the vendor app (fixed default there); the device accepts it
    via preferences.
    """

    _attr_translation_key = "expected_heating_temperature"
    _attr_icon = "mdi:fire"
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_native_min_value = 10
    _attr_native_max_value = 40
    _attr_native_step = 1
    _attr_mode = NumberMode.SLIDER

    def __init__(self, coordinator: RFeederCoordinator, device_id: str) -> None:
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_expected_heating_temperature"

    def _preferences(self) -> dict[str, Any]:
        value = self.shadow_value("10:11:7")
        return value if isinstance(value, dict) else {}

    @property
    def native_value(self) -> float | None:
        value = self._preferences().get("expectedHeatingTemperature")
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    async def async_set_native_value(self, value: float) -> None:
        prefs = self._preferences()
        await self.coordinator.async_write_attributes(
            self._device_id,
            proto.CLUSTER_FEED,
            {
                proto.ATTR_PREFERENCES: proto.encode_feed_preferences(
                    feed_duration_seconds=int(prefs.get("feedDurationSeconds", 300)),
                    heat_before_feeding=bool(prefs.get("heatBeforeFeeding", False)),
                    expected_heating_temperature=float(value),
                )
            },
        )
