"""Binary sensors for the RFeeder integration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import RFeederCoordinator
from .entity import RFeederEntity


def _shadow_value(item: dict[str, Any], key: str) -> Any:
    entry = item.get("shadow", {}).get(key)
    if not isinstance(entry, dict):
        return None
    return entry.get("value")


def _online(item: dict[str, Any]) -> bool | None:
    online = item.get("online")
    if isinstance(online, list) and online:
        value = online[0].get("onoffline")
        return None if value is None else bool(value)
    return None


@dataclass(frozen=True, kw_only=True)
class RFeederBinarySensorDescription(BinarySensorEntityDescription):
    value_fn: Callable[[dict[str, Any]], bool | None]


DEVICE_BINARY_SENSORS: tuple[RFeederBinarySensorDescription, ...] = (
    RFeederBinarySensorDescription(
        key="online",
        translation_key="online",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_online,
    ),
    RFeederBinarySensorDescription(
        key="feeding",
        translation_key="feeding",
        icon="mdi:food",
        value_fn=lambda item: _shadow_value(item, "10:11:2")
        not in (None, "STOPPED", "FEED_STATE_STOPPED"),
    ),
    RFeederBinarySensorDescription(
        key="pet_detected",
        translation_key="pet_detected",
        icon="mdi:paw",
        value_fn=lambda item: bool(_shadow_value(item, "10:11:9"))
        if _shadow_value(item, "10:11:9") is not None
        else None,
    ),
    RFeederBinarySensorDescription(
        key="door_fault",
        translation_key="door_fault",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda item: _shadow_value(item, "10:11:3")
        in ("FAULT", "DOOR_STATE_FAULT"),
    ),
    RFeederBinarySensorDescription(
        key="tray_fault",
        translation_key="tray_fault",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda item: _shadow_value(item, "10:11:4")
        in ("FAULT", "TRAY_STATE_FAULT", "NO_TRAY", "TRAY_STATE_NO_TRAY", "MISALIGNED", "TRAY_STATE_MISALIGNED"),
    ),
    RFeederBinarySensorDescription(
        key="feed_fault",
        translation_key="feed_fault",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda item: _shadow_value(item, "10:11:2")
        in ("FAULT", "FEED_STATE_FAULT"),
    ),
    RFeederBinarySensorDescription(
        key="temperature_sensor_fault",
        translation_key="temperature_sensor_fault",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda item: _shadow_value(item, "10:11:16")
        not in (None, 0, "NONE", "TEMPERATURE_SENSOR_FAULT_NONE"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: RFeederCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        RFeederBinarySensor(coordinator, device_id, description)
        for device_id in coordinator.data.get("devices", {})
        for description in DEVICE_BINARY_SENSORS
    )


class RFeederBinarySensor(RFeederEntity, BinarySensorEntity):
    entity_description: RFeederBinarySensorDescription

    def __init__(
        self,
        coordinator: RFeederCoordinator,
        device_id: str,
        description: RFeederBinarySensorDescription,
    ) -> None:
        super().__init__(coordinator, device_id)
        self.entity_description = description
        self._attr_unique_id = f"{device_id}_{description.key}"

    @property
    def is_on(self) -> bool | None:
        item = self.coordinator.data.get("devices", {}).get(self._device_id)
        if not item:
            return None
        return self.entity_description.value_fn(item)
