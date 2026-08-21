"""Sensors for the RFeeder integration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Callable

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfTemperature, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import RFeederCoordinator
from .entity import RFeederEntity


def _shadow_int(item: dict[str, Any], key: str) -> int | None:
    entry = item.get("shadow", {}).get(key)
    if not isinstance(entry, dict):
        return None
    value = entry.get("value")
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _shadow_str(item: dict[str, Any], key: str) -> str | None:
    entry = item.get("shadow", {}).get(key)
    if not isinstance(entry, dict):
        return None
    value = entry.get("value")
    return str(value) if value is not None else None


def _ms_to_datetime(value: Any) -> datetime | None:
    try:
        ms = int(value)
    except (TypeError, ValueError):
        return None
    if ms <= 0:
        return None
    return datetime.fromtimestamp(ms / 1000, UTC)


def _last_record(item: dict[str, Any]) -> dict[str, Any] | None:
    records = item.get("records")
    if isinstance(records, list) and records:
        return records[0]
    return None


def _last_feeding_time(item: dict[str, Any]) -> datetime | None:
    record = _last_record(item)
    if not record:
        return None
    return _ms_to_datetime(record.get("startTime"))


def _firmware(item: dict[str, Any]) -> str | None:
    entry = item.get("shadow", {}).get("10:10:1")
    if not isinstance(entry, dict):
        return None
    value = entry.get("value")
    if isinstance(value, dict):
        return value.get("name")
    return None


def _wifi_ssid(item: dict[str, Any]) -> str | None:
    entry = item.get("shadow", {}).get("10:10:9")
    if not isinstance(entry, dict):
        return None
    value = entry.get("value")
    if isinstance(value, dict):
        return value.get("ssid")
    return None


@dataclass(frozen=True, kw_only=True)
class RFeederSensorDescription(SensorEntityDescription):
    value_fn: Callable[[dict[str, Any]], Any]


DEVICE_SENSORS: tuple[RFeederSensorDescription, ...] = (
    RFeederSensorDescription(
        key="global_state",
        translation_key="global_state",
        icon="mdi:robot-outline",
        value_fn=lambda item: _shadow_str(item, "10:11:1"),
    ),
    RFeederSensorDescription(
        key="feed_state",
        translation_key="feed_state",
        icon="mdi:food-drumstick-outline",
        value_fn=lambda item: _shadow_str(item, "10:11:2"),
    ),
    RFeederSensorDescription(
        key="door_state",
        translation_key="door_state",
        icon="mdi:door",
        value_fn=lambda item: _shadow_str(item, "10:11:3"),
    ),
    RFeederSensorDescription(
        key="tray_state",
        translation_key="tray_state",
        icon="mdi:rotate-360",
        value_fn=lambda item: _shadow_str(item, "10:11:4"),
    ),
    RFeederSensorDescription(
        key="actual_temperature",
        translation_key="actual_temperature",
        icon="mdi:thermometer",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda item: _shadow_int(item, "10:11:11"),
    ),
    RFeederSensorDescription(
        key="tray_temperature",
        translation_key="tray_temperature",
        icon="mdi:thermometer-lines",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda item: _shadow_int(item, "10:11:20"),
    ),
    RFeederSensorDescription(
        key="thermoelectric_temperature",
        translation_key="thermoelectric_temperature",
        icon="mdi:snowflake-thermometer",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda item: _shadow_int(item, "10:11:19"),
    ),
    RFeederSensorDescription(
        key="temperature_trend",
        translation_key="temperature_trend",
        icon="mdi:chart-line",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda item: _shadow_str(item, "10:11:18"),
    ),
    RFeederSensorDescription(
        key="feedings_today",
        translation_key="feedings_today",
        icon="mdi:counter",
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda item: (item.get("today") or {}).get("feedingCount"),
    ),
    RFeederSensorDescription(
        key="last_feeding",
        translation_key="last_feeding",
        icon="mdi:history",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=_last_feeding_time,
    ),
    RFeederSensorDescription(
        key="power_supply",
        translation_key="power_supply",
        icon="mdi:power-plug-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda item: _shadow_str(item, "10:10:7"),
    ),
    RFeederSensorDescription(
        key="battery_level",
        translation_key="battery_level",
        icon="mdi:battery",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda item: _shadow_int(item, "10:10:8"),
    ),
    RFeederSensorDescription(
        key="wifi_ssid",
        translation_key="wifi_ssid",
        icon="mdi:wifi",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_wifi_ssid,
    ),
    RFeederSensorDescription(
        key="wifi_rssi",
        translation_key="wifi_rssi",
        icon="mdi:wifi-strength-2",
        native_unit_of_measurement="dBm",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda item: _shadow_int(item, "10:10:10"),
    ),
    RFeederSensorDescription(
        key="firmware_version",
        translation_key="firmware_version",
        icon="mdi:chip",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_firmware,
    ),
    RFeederSensorDescription(
        key="tray_compartments",
        translation_key="tray_compartments",
        icon="mdi:view-grid-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda item: _shadow_int(item, "10:11:5"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: RFeederCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[SensorEntity] = []
    for device_id in coordinator.data.get("devices", {}):
        entities.extend(
            RFeederSensor(coordinator, device_id, description) for description in DEVICE_SENSORS
        )
        entities.append(RFeederNextScheduleSensor(coordinator, device_id))
    async_add_entities(entities)


class RFeederSensor(RFeederEntity, SensorEntity):
    entity_description: RFeederSensorDescription

    def __init__(
        self,
        coordinator: RFeederCoordinator,
        device_id: str,
        description: RFeederSensorDescription,
    ) -> None:
        super().__init__(coordinator, device_id)
        self.entity_description = description
        self._attr_unique_id = f"{device_id}_{description.key}"

    @property
    def native_value(self) -> Any:
        item = self.coordinator.data.get("devices", {}).get(self._device_id)
        if not item:
            return None
        return self.entity_description.value_fn(item)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        item = self.coordinator.data.get("devices", {}).get(self._device_id)
        if not item:
            return None
        if self.entity_description.key == "feedings_today":
            stats = item.get("statistics") or {}
            plates = {
                f"plate_{plate.get('plateId')}": plate.get("feedingCount")
                for plate in stats.get("plateList", [])
                if isinstance(plate, dict)
            }
            return {"plates": plates} if plates else None
        if self.entity_description.key == "last_feeding":
            record = _last_record(item)
            if not record:
                return None
            events = record.get("events") or []
            return {
                "task_status": record.get("taskStatus"),
                "task_source": record.get("taskSource"),
                "events": [event.get("eventType") for event in events],
            }
        return None


class RFeederNextScheduleSensor(RFeederEntity, SensorEntity):
    _attr_translation_key = "next_scheduled_feeding"
    _attr_icon = "mdi:calendar-clock-outline"

    def __init__(self, coordinator: RFeederCoordinator, device_id: str) -> None:
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_next_scheduled_feeding"

    def _schedules(self) -> list[dict[str, Any]]:
        entry = self.shadow.get("10:11:13")
        if not isinstance(entry, dict):
            return []
        value = entry.get("value")
        if not isinstance(value, dict):
            return []
        schedules = value.get("schedules")
        return schedules if isinstance(schedules, list) else []

    @property
    def native_value(self) -> str | None:
        now = datetime.now(UTC)
        best: str | None = None
        best_dt: datetime | None = None
        for schedule in self._schedules():
            if not isinstance(schedule, dict) or not schedule.get("enabled", True):
                continue
            start = schedule.get("start") or {}
            hour, minute = start.get("hour"), start.get("minute")
            if hour is None or minute is None:
                continue
            weekdays = int(schedule.get("weekdays", 0) or 0)
            candidate = _next_occurrence(now, int(hour), int(minute), weekdays)
            if candidate and (best_dt is None or candidate < best_dt):
                best_dt = candidate
                best = f"{int(hour):02d}:{int(minute):02d}"
        return best

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        schedules = []
        for schedule in self._schedules():
            if not isinstance(schedule, dict):
                continue
            start = schedule.get("start") or {}
            hour = int(start.get("hour", 0))
            minute = int(start.get("minute", 0))
            weekdays = int(schedule.get("weekdays", 0) or 0)
            schedules.append(
                {
                    "id": schedule.get("id"),
                    "time_local": _utc_to_local_str(hour, minute),
                    "time_utc": f"{hour:02d}:{minute:02d}",
                    "enabled": schedule.get("enabled"),
                    "weekdays": _weekday_names(weekdays),
                    "weekdays_mask": weekdays,
                    "feed_options": schedule.get("feedOptions"),
                }
            )
        return {"schedules": schedules}


_WEEKDAY_NAMES = ("sun", "mon", "tue", "wed", "thu", "fri", "sat")


def _weekday_names(mask: int) -> list[str]:
    return [name for bit, name in enumerate(_WEEKDAY_NAMES) if mask & (1 << bit)]


def _utc_to_local_str(hour: int, minute: int) -> str:
    """Convert a UTC wall time to the local (HA timezone) HH:MM string."""
    from homeassistant.util import dt as dt_util

    now = dt_util.now()
    utc_dt = now.astimezone(UTC).replace(hour=hour, minute=minute, second=0, microsecond=0)
    local = utc_dt.astimezone(now.tzinfo)
    return f"{local.hour:02d}:{local.minute:02d}"


def _next_occurrence(now: datetime, hour: int, minute: int, weekdays_mask: int) -> datetime | None:
    """Compute the next occurrence of a UTC schedule; weekdays bit0=Sun..bit6=Sat."""
    from datetime import timedelta

    for day_offset in range(8):
        candidate = (now + timedelta(days=day_offset)).replace(
            hour=hour, minute=minute, second=0, microsecond=0
        )
        if candidate <= now:
            continue
        if weekdays_mask == 0:
            return candidate
        # python weekday(): Mon=0..Sun=6; mask: bit0=Sun..bit6=Sat
        bit = (candidate.weekday() + 1) % 7  # Mon->1, ..., Sat->6, Sun->0
        if weekdays_mask & (1 << bit):
            return candidate
    return None
