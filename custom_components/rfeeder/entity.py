"""Base entity for the RFeeder integration."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import RFeederCoordinator


class RFeederEntity(CoordinatorEntity[RFeederCoordinator]):
    """Base class for entities bound to one feeder device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: RFeederCoordinator, device_id: str) -> None:
        super().__init__(coordinator)
        self._device_id = device_id

    @property
    def device_data(self) -> dict[str, Any]:
        return self.coordinator.data.get("devices", {}).get(self._device_id, {})

    @property
    def shadow(self) -> dict[str, Any]:
        shadow = self.device_data.get("shadow")
        return shadow if isinstance(shadow, dict) else {}

    def shadow_value(self, key: str, default: Any = None) -> Any:
        entry = self.shadow.get(key)
        if not isinstance(entry, dict):
            return default
        return entry.get("value", default)

    @property
    def device_info(self) -> DeviceInfo:
        info = self.device_data.get("info", {})
        firmware = self.shadow_value("10:10:1")
        sw_version = firmware.get("name") if isinstance(firmware, dict) else None
        return DeviceInfo(
            identifiers={(DOMAIN, self._device_id)},
            manufacturer="Robotail",
            model="Smart Dual-Temp Feeder",
            name=info.get("deviceName") or self._device_id,
            serial_number=self._device_id,
            sw_version=sw_version,
        )

    @property
    def available(self) -> bool:
        return super().available and bool(self.device_data)
