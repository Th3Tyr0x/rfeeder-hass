"""Switches for writable boolean attributes of the RFeeder."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import proto
from .const import DOMAIN
from .coordinator import RFeederCoordinator
from .entity import RFeederEntity


@dataclass(frozen=True, kw_only=True)
class RFeederSwitchDescription(SwitchEntityDescription):
    cluster: int
    attribute: int
    shadow_key: str


DEVICE_SWITCHES: tuple[RFeederSwitchDescription, ...] = (
    RFeederSwitchDescription(
        key="temperature_control",
        translation_key="temperature_control",
        icon="mdi:snowflake",
        cluster=proto.CLUSTER_FEED,
        attribute=proto.ATTR_TEMPERATURE_CONTROL_ON,
        shadow_key="10:11:10",
    ),
    RFeederSwitchDescription(
        key="pet_detection",
        translation_key="pet_detection",
        icon="mdi:paw",
        cluster=proto.CLUSTER_FEED,
        attribute=proto.ATTR_PET_DETECTION_ON,
        shadow_key="10:11:8",
    ),
    RFeederSwitchDescription(
        key="auto_feeding",
        translation_key="auto_feeding",
        icon="mdi:robot-happy-outline",
        cluster=proto.CLUSTER_FEED,
        attribute=proto.ATTR_AUTO_FEEDING_ENABLED,
        shadow_key="10:11:17",
    ),
    RFeederSwitchDescription(
        key="heat_before_feed_now",
        translation_key="heat_before_feed_now",
        icon="mdi:fire",
        cluster=proto.CLUSTER_FEED,
        attribute=proto.ATTR_HEAT_BEFORE_FEED_NOW,
        shadow_key="10:11:22",
    ),
    RFeederSwitchDescription(
        key="automatic_update",
        translation_key="automatic_update",
        icon="mdi:update",
        cluster=proto.CLUSTER_DEVICE,
        attribute=5,
        shadow_key="10:10:5",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: RFeederCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        RFeederSwitch(coordinator, device_id, description)
        for device_id in coordinator.data.get("devices", {})
        for description in DEVICE_SWITCHES
    )


class RFeederSwitch(RFeederEntity, SwitchEntity):
    entity_description: RFeederSwitchDescription

    def __init__(
        self,
        coordinator: RFeederCoordinator,
        device_id: str,
        description: RFeederSwitchDescription,
    ) -> None:
        super().__init__(coordinator, device_id)
        self.entity_description = description
        self._attr_unique_id = f"{device_id}_{description.key}"

    @property
    def is_on(self) -> bool | None:
        value = self.shadow_value(self.entity_description.shadow_key)
        if value is None:
            return None
        return bool(value)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._write(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._write(False)

    async def _write(self, enabled: bool) -> None:
        await self.coordinator.async_write_attributes(
            self._device_id,
            self.entity_description.cluster,
            {self.entity_description.attribute: proto.encode_primitive_data(enabled)},
        )
