"""Button entities (device actions) for the RFeeder."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import proto
from .const import DOMAIN
from .coordinator import RFeederCoordinator
from .entity import RFeederEntity

DEVICE_BUTTONS: tuple[ButtonEntityDescription, ...] = (
    ButtonEntityDescription(
        key="feed_now",
        translation_key="feed_now",
        icon="mdi:food",
    ),
    ButtonEntityDescription(
        key="stop_feeding",
        translation_key="stop_feeding",
        icon="mdi:stop",
    ),
    ButtonEntityDescription(
        key="skip_heating",
        translation_key="skip_heating",
        icon="mdi:fire-off",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: RFeederCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        RFeederButton(coordinator, device_id, description)
        for device_id in coordinator.data.get("devices", {})
        for description in DEVICE_BUTTONS
    )


class RFeederButton(RFeederEntity, ButtonEntity):
    entity_description: ButtonEntityDescription

    def __init__(
        self,
        coordinator: RFeederCoordinator,
        device_id: str,
        description: ButtonEntityDescription,
    ) -> None:
        super().__init__(coordinator, device_id)
        self.entity_description = description
        self._attr_unique_id = f"{device_id}_{description.key}"

    async def async_press(self) -> None:
        key = self.entity_description.key
        if key == "feed_now":
            prefs = self.shadow_value("10:11:7")
            prefs = prefs if isinstance(prefs, dict) else {}
            tray = self.shadow_value("10:11:6")
            try:
                tray_default = int(tray)
            except (TypeError, ValueError):
                tray_default = 1
            await self.coordinator.async_feed_now(
                self._device_id,
                tray_compartment_index=self.coordinator.get_compartment(
                    self._device_id, tray_default
                ),
                feed_duration_seconds=int(prefs.get("feedDurationSeconds", 300)),
                heat_before_feeding=bool(
                    self.shadow_value("10:11:22") or prefs.get("heatBeforeFeeding", False)
                ),
                expected_heating_temperature=float(
                    prefs.get("expectedHeatingTemperature", 24)
                ),
            )
        elif key == "stop_feeding":
            await self.coordinator.async_simple_service(self._device_id, proto.SERVICE_STOP_FEEDING)
        elif key == "skip_heating":
            await self.coordinator.async_simple_service(self._device_id, proto.SERVICE_SKIP_HEATING)
