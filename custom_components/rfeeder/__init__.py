from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import aiohttp_client
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from . import proto
from .api import UcspClient
from .const import (
    CONF_AREA,
    CONF_APP_ID,
    CONF_APP_SECRET,
    CONF_BASE_URL,
    CONF_CLIENT_ID,
    CONF_PRODUCT_KEY,
    CONF_PW_SALT,
    CONF_TIMESTAMP_URL,
    DOMAIN,
    PLATFORMS,
    SERVICE_ADD_SCHEDULE,
    SERVICE_FEED_NOW,
    SERVICE_REMOVE_SCHEDULE,
    SERVICE_SET_SCHEDULE_ENABLED,
    SERVICE_SYNC_WEEKLY_PLAN,
)
from .coordinator import RFeederCoordinator
from .mqtt_client import MqttError

_LOGGER = logging.getLogger(__name__)

CONF_DEVICE_ID = "device_id"

# Entity unique_ids from pre-release versions that no longer exist.
_LEGACY_ENTITY_SUFFIXES = ("_expected_cooling_temperature", "_expected_heating_temperature")

_WEEKDAY_BITS = {
    "sun": 0,
    "mon": 1,
    "tue": 2,
    "wed": 3,
    "thu": 4,
    "fri": 5,
    "sat": 6,
}


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    config = {
        CONF_AREA: entry.data.get(CONF_AREA),
        CONF_APP_ID: entry.data.get(CONF_APP_ID),
        CONF_APP_SECRET: entry.data.get(CONF_APP_SECRET),
        CONF_BASE_URL: entry.data.get(CONF_BASE_URL),
        CONF_CLIENT_ID: entry.data.get(CONF_CLIENT_ID),
        CONF_PRODUCT_KEY: entry.data.get(CONF_PRODUCT_KEY),
        CONF_PW_SALT: entry.data.get(CONF_PW_SALT),
        CONF_TIMESTAMP_URL: entry.data.get(CONF_TIMESTAMP_URL),
    }
    missing = [key for key, value in config.items() if key != CONF_AREA and not value]
    if missing:
        _LOGGER.error("RFeeder setup missing API field(s): %s", ", ".join(missing))
        return False

    # Dedicated session: the client intentionally drops pooled connections to
    # work around the vendor's inconsistent DNS round-robin backends; that must
    # not affect the shared HA session used by other integrations.
    session = aiohttp_client.async_create_clientsession(hass)
    client = UcspClient(
        session,
        account=entry.data[CONF_USERNAME],
        password=entry.data[CONF_PASSWORD],
        config=config,
    )
    coordinator = RFeederCoordinator(hass, client, entry.data[CONF_PRODUCT_KEY], entry)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    _cleanup_legacy_entities(hass, entry)
    await _register_card(hass)

    coordinator.start_mqtt()
    entry.async_on_unload(coordinator.stop_mqtt)

    _register_services(hass)
    return True


async def _register_card(hass: HomeAssistant) -> None:
    """Serve the bundled Lovelace card and load it on every dashboard page."""
    card_path = Path(__file__).parent / "www" / "rfeeder-card.js"
    if not card_path.is_file():
        _LOGGER.warning("RFeeder card file missing: %s", card_path)
        return
    url = f"/{DOMAIN}_www/rfeeder-card.js"
    try:
        from homeassistant.components.http import StaticPathConfig

        await hass.http.async_register_static_paths(
            [StaticPathConfig(url, str(card_path), cache_headers=False)]
        )
    except (ImportError, AttributeError) as err:
        _LOGGER.warning("Could not serve the RFeeder card file: %s", err)
        return
    try:
        from homeassistant.components import frontend

        frontend.add_extra_js_url(hass, url)
    except (ImportError, AttributeError) as err:
        _LOGGER.warning(
            "Could not auto-register the RFeeder card; add the resource manually: %s (%s)",
            url,
            err,
        )


def _cleanup_legacy_entities(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove entity-registry entries of entities removed in later releases."""
    registry = er.async_get(hass)
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        if entity.unique_id.endswith(_LEGACY_ENTITY_SUFFIXES):
            _LOGGER.info("Removing legacy entity %s", entity.entity_id)
            registry.async_remove(entity.entity_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        coordinator = hass.data[DOMAIN].pop(entry.entry_id, None)
        if coordinator is not None:
            coordinator.stop_mqtt()
    return unload_ok


def _coordinators(hass: HomeAssistant) -> list[RFeederCoordinator]:
    return list(hass.data.get(DOMAIN, {}).values())


def _find_device(hass: HomeAssistant, device_id: str | None) -> tuple[RFeederCoordinator, str]:
    """Resolve the (coordinator, device_id) for a service call."""
    for coordinator in _coordinators(hass):
        devices = (coordinator.data or {}).get("devices", {})
        if device_id is None and devices:
            return coordinator, next(iter(devices))
        if device_id in devices:
            return coordinator, device_id
    raise HomeAssistantError(f"Unknown RFeeder device: {device_id}")


def _weekday_mask(value: Any) -> int:
    """Accept a list of weekday names (["mon","tue",...]) or a raw bitmask int."""
    if value in (None, ""):
        return 0
    if isinstance(value, int):
        return value
    mask = 0
    for name in value if isinstance(value, list) else [value]:
        key = str(name).strip().lower()[:3]
        if key not in _WEEKDAY_BITS:
            raise HomeAssistantError(f"Unknown weekday: {name!r} (use mon..sun)")
        mask |= 1 << _WEEKDAY_BITS[key]
    return mask


def _local_time_to_utc(time_str: str) -> tuple[int, int]:
    """Convert a local "HH:MM" wall time (HA timezone) to UTC hour/minute."""
    parts = str(time_str).strip().split(":")
    if len(parts) < 2:
        raise HomeAssistantError(f"Invalid time {time_str!r} (expected HH:MM)")
    now = dt_util.now()
    local = now.replace(hour=int(parts[0]), minute=int(parts[1]), second=0, microsecond=0)
    utc = local.astimezone(dt_util.UTC)
    return utc.hour, utc.minute


def _register_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, SERVICE_FEED_NOW):
        return

    async def handle_feed_now(call: ServiceCall) -> None:
        coordinator, device_id = _find_device(hass, call.data.get(CONF_DEVICE_ID))
        prefs = {}
        entry = coordinator.data["devices"][device_id].get("shadow", {}).get("10:11:7")
        if isinstance(entry, dict) and isinstance(entry.get("value"), dict):
            prefs = entry["value"]
        tray = coordinator.data["devices"][device_id].get("shadow", {}).get("10:11:6", {})
        tray_index = tray.get("value", 1) if isinstance(tray, dict) else 1
        try:
            tray_default = int(tray_index)
        except (TypeError, ValueError):
            tray_default = 1
        duration_minutes = call.data.get("feed_duration_minutes")
        duration_seconds = (
            int(round(duration_minutes * 60))
            if duration_minutes is not None
            else int(prefs.get("feedDurationSeconds", 300))
        )
        try:
            await coordinator.async_feed_now(
                device_id,
                tray_compartment_index=call.data.get(
                    "tray_compartment_index",
                    coordinator.get_compartment(device_id, tray_default),
                ),
                feed_duration_seconds=duration_seconds,
                heat_before_feeding=call.data.get("heat_before_feeding", False),
                expected_heating_temperature=float(
                    prefs.get("expectedHeatingTemperature", 24)
                ),
            )
        except MqttError as err:
            raise HomeAssistantError(str(err)) from err

    async def handle_add_schedule(call: ServiceCall) -> None:
        coordinator, device_id = _find_device(hass, call.data.get(CONF_DEVICE_ID))
        if call.data.get("time"):
            hour, minute = _local_time_to_utc(call.data["time"])
        elif "hour" in call.data and "minute" in call.data:
            hour, minute = call.data["hour"], call.data["minute"]
        else:
            raise HomeAssistantError("Provide 'time' (local HH:MM) or 'hour'+'minute' (UTC)")
        schedule = {
            "id": 0,
            "hour": hour,
            "minute": minute,
            "enabled": call.data.get("enabled", True),
            "weekdays": _weekday_mask(call.data.get("weekdays")),
            "feedOptions": {
                "trayCompartmentIndex": call.data.get("tray_compartment_index", 1),
                "feedDurationSeconds": int(
                    round(call.data.get("feed_duration_minutes", 5) * 60)
                ),
                "heatBeforeFeeding": call.data.get("heat_before_feeding", False),
                "expectedHeatingTemperature": 24,
            },
        }
        try:
            await coordinator.async_set_schedules(
                device_id, proto.SERVICE_ADD_SCHEDULES, [schedule]
            )
        except MqttError as err:
            raise HomeAssistantError(str(err)) from err

    async def handle_remove_schedule(call: ServiceCall) -> None:
        coordinator, device_id = _find_device(hass, call.data.get(CONF_DEVICE_ID))
        try:
            await coordinator.async_remove_schedules(device_id, [int(call.data["schedule_id"])])
        except MqttError as err:
            raise HomeAssistantError(str(err)) from err

    async def handle_sync_weekly_plan(call: ServiceCall) -> None:
        coordinator, device_id = _find_device(hass, call.data.get(CONF_DEVICE_ID))
        plan = call.data.get("plan") or []
        if not isinstance(plan, list):
            raise HomeAssistantError("plan must be a list of schedule entries")

        schedules: list[dict[str, Any]] = []
        for i, entry in enumerate(plan):
            if not isinstance(entry, dict):
                raise HomeAssistantError(f"plan entry #{i} is not a mapping")
            if "time" in entry and entry["time"]:
                hour, minute = _local_time_to_utc(entry["time"])
            elif "hour" in entry and "minute" in entry:
                hour, minute = int(entry["hour"]), int(entry["minute"])
            else:
                raise HomeAssistantError(f"plan entry #{i} misses 'time'")
            compartment = int(entry.get("compartment", entry.get("tray_compartment_index", 1)))
            if not 1 <= compartment <= 4:
                raise HomeAssistantError(f"plan entry #{i}: compartment must be 1-4")
            schedules.append(
                {
                    "id": 0,
                    "hour": hour,
                    "minute": minute,
                    "enabled": bool(entry.get("enabled", True)),
                    "weekdays": _weekday_mask(entry.get("weekdays")),
                    "feedOptions": {
                        "trayCompartmentIndex": compartment,
                        "feedDurationSeconds": int(
                            round(float(entry.get("feed_duration_minutes", 5)) * 60)
                        ),
                        "heatBeforeFeeding": bool(entry.get("heat_before_feeding", False)),
                        "expectedHeatingTemperature": 24,
                    },
                }
            )

        try:
            if call.data.get("replace", True):
                shadow = coordinator.data["devices"][device_id].get("shadow", {})
                existing = shadow.get("10:11:13", {})
                current = []
                if isinstance(existing, dict) and isinstance(existing.get("value"), dict):
                    current = existing["value"].get("schedules") or []
                ids = [int(s["id"]) for s in current if isinstance(s, dict) and s.get("id") is not None]
                if ids:
                    await coordinator.async_remove_schedules(device_id, ids)
            if schedules:
                await coordinator.async_set_schedules(
                    device_id, proto.SERVICE_ADD_SCHEDULES, schedules
                )
        except MqttError as err:
            raise HomeAssistantError(str(err)) from err

    async def handle_set_schedule_enabled(call: ServiceCall) -> None:
        coordinator, device_id = _find_device(hass, call.data.get(CONF_DEVICE_ID))
        schedule_id = int(call.data["schedule_id"])
        enabled = bool(call.data["enabled"])
        shadow = coordinator.data["devices"][device_id].get("shadow", {})
        entry = shadow.get("10:11:13")
        schedules = []
        if isinstance(entry, dict) and isinstance(entry.get("value"), dict):
            schedules = entry["value"].get("schedules") or []
        updated = []
        found = False
        for item in schedules:
            if not isinstance(item, dict):
                continue
            if item.get("id") == schedule_id:
                start = item.get("start") or {}
                options = item.get("feedOptions") or {}
                updated.append(
                    {
                        "id": schedule_id,
                        "hour": start.get("hour", 0),
                        "minute": start.get("minute", 0),
                        "enabled": enabled,
                        "weekdays": item.get("weekdays", 0),
                        "feedOptions": {
                            "trayCompartmentIndex": options.get("trayCompartmentIndex", 1),
                            "feedDurationSeconds": options.get("feedDurationSeconds", 300),
                            "heatBeforeFeeding": options.get("heatBeforeFeeding", False),
                            "expectedHeatingTemperature": options.get(
                                "expectedHeatingTemperature", 24
                            ),
                        },
                    }
                )
                found = True
            else:
                start = item.get("start") or {}
                options = item.get("feedOptions") or {}
                updated.append(
                    {
                        "id": item.get("id", 0),
                        "hour": start.get("hour", 0),
                        "minute": start.get("minute", 0),
                        "enabled": item.get("enabled", True),
                        "weekdays": item.get("weekdays", 0),
                        "feedOptions": {
                            "trayCompartmentIndex": options.get("trayCompartmentIndex", 1),
                            "feedDurationSeconds": options.get("feedDurationSeconds", 300),
                            "heatBeforeFeeding": options.get("heatBeforeFeeding", False),
                            "expectedHeatingTemperature": options.get(
                                "expectedHeatingTemperature", 24
                            ),
                        },
                    }
                )
        if not found:
            raise HomeAssistantError(f"Schedule {schedule_id} not found")
        try:
            await coordinator.async_set_schedules(
                device_id, proto.SERVICE_UPDATE_SCHEDULES, updated
            )
        except MqttError as err:
            raise HomeAssistantError(str(err)) from err

    hass.services.async_register(
        DOMAIN,
        SERVICE_FEED_NOW,
        handle_feed_now,
        schema=vol.Schema(
            {
                vol.Optional(CONF_DEVICE_ID): str,
                vol.Optional("tray_compartment_index"): vol.Coerce(int),
                vol.Optional("feed_duration_minutes"): vol.Coerce(float),
                vol.Optional("heat_before_feeding"): bool,
            }
        ),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_ADD_SCHEDULE,
        handle_add_schedule,
        schema=vol.Schema(
            {
                vol.Optional(CONF_DEVICE_ID): str,
                vol.Optional("time"): str,
                vol.Optional("hour"): vol.All(vol.Coerce(int), vol.Range(min=0, max=23)),
                vol.Optional("minute"): vol.All(vol.Coerce(int), vol.Range(min=0, max=59)),
                vol.Optional("weekdays", default=0): vol.Any(vol.Coerce(int), [str]),
                vol.Optional("enabled", default=True): bool,
                vol.Optional("tray_compartment_index", default=1): vol.Coerce(int),
                vol.Optional("feed_duration_minutes", default=5): vol.Coerce(float),
                vol.Optional("heat_before_feeding", default=False): bool,
            }
        ),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_REMOVE_SCHEDULE,
        handle_remove_schedule,
        schema=vol.Schema(
            {
                vol.Optional(CONF_DEVICE_ID): str,
                vol.Required("schedule_id"): vol.Coerce(int),
            }
        ),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SYNC_WEEKLY_PLAN,
        handle_sync_weekly_plan,
        schema=vol.Schema(
            {
                vol.Optional(CONF_DEVICE_ID): str,
                vol.Required("plan"): [dict],
                vol.Optional("replace", default=True): bool,
            }
        ),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_SCHEDULE_ENABLED,
        handle_set_schedule_enabled,
        schema=vol.Schema(
            {
                vol.Optional(CONF_DEVICE_ID): str,
                vol.Required("schedule_id"): vol.Coerce(int),
                vol.Required("enabled"): bool,
            }
        ),
    )
