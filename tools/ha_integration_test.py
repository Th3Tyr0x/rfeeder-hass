
# Credentials kommen aus den Umgebungsvariablen RFEEDER_ACCOUNT / RFEEDER_PASSWORD.
#!/usr/bin/env python3
"""Boot a real Home Assistant core and set up the rfeeder integration end-to-end.

Usage:
    /tmp/opencode/hass-venv/bin/python tools/ha_integration_test.py

Creates a config entry via the real config flow (validating credentials against
the live cloud), sets up all platforms and prints the resulting entities.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
import sys
import tempfile

CONFIG_DIR = tempfile.mkdtemp(prefix="rfeeder-hass-")
REPO = Path(__file__).resolve().parent.parent

cc = Path(CONFIG_DIR) / "custom_components"
cc.mkdir(parents=True, exist_ok=True)
(cc / "rfeeder").symlink_to(REPO / "custom_components" / "rfeeder")

# minimale configuration.yaml, damit der Bootstrap nur den Kern laedt
(Path(CONFIG_DIR) / "configuration.yaml").write_text("homeassistant:\ndefault_config:\n")

from homeassistant import config_entries, core, loader  # noqa: E402
from homeassistant.helpers import frame  # noqa: E402
from homeassistant.setup import async_setup_component  # noqa: E402

ACCOUNT = os.environ["RFEEDER_ACCOUNT"]
PASSWORD = os.environ["RFEEDER_PASSWORD"]


async def main() -> int:
    hass = core.HomeAssistant(CONFIG_DIR)
    loader.async_setup(hass)
    frame.async_setup(hass)
    # so initialisiert es der Bootstrap: ConfigEntries + Registries manuell anlegen
    hass.config_entries = config_entries.ConfigEntries(hass, {})
    from homeassistant.helpers import area_registry, device_registry, entity_registry, issue_registry

    hass.data[device_registry.DATA_REGISTRY] = device_registry.DeviceRegistry(hass)
    await area_registry.async_load(hass)
    await device_registry.async_load(hass)
    await entity_registry.async_load(hass)
    await issue_registry.async_load(hass)
    await loader.async_get_custom_components(hass)
    await hass.async_start()
    try:
        assert await async_setup_component(hass, "homeassistant", {})
        await async_setup_component(hass, "network", {})
        flow = await hass.config_entries.flow.async_init(
            "rfeeder", context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            flow["flow_id"],
            {"username": ACCOUNT, "password": PASSWORD, "area": "DE"},
        )
        print("config flow:", result.get("type"), "|", result.get("title"))
        if result.get("type") != "create_entry":
            print("ERRORS:", result.get("errors"))
            return 1

        entry = result["result"]
        coordinator = hass.data["rfeeder"][entry.entry_id]
        data = coordinator.data or {}
        print("devices:", list(data.get("devices", {})))

        # Plattformen + MQTT aufsetzen lassen
        for _ in range(45):
            await asyncio.sleep(1)
            if hass.states.async_entity_ids_count() > 30:
                break

        states = hass.states.async_all()
        print(f"\n{len(states)} entities:")
        for state in sorted(states, key=lambda s: s.entity_id):
            print(f"  {state.entity_id:55} = {state.state}")

        # Fach-Auswahl testen: Fach 3 waehlen, dann feed_now aufrufen
        select_id = "select.smart_dual_temp_feeder_feed_compartment"
        sel = hass.states.get(select_id)
        print("\ncompartment select:", sel.state if sel else None, sel.attributes.get("options") if sel else None)
        await hass.services.async_call(
            "select", "select_option", {"entity_id": select_id, "option": "3"}, blocking=True
        )
        print("compartment after select:", hass.states.get(select_id).state)

        # Kuehlstufen-Select pruefen
        cool_id = "select.smart_dual_temp_feeder_cooling"
        cool = hass.states.get(cool_id)
        print("cooling select:", cool.state if cool else None, cool.attributes.get("options") if cool else None)
        await hass.services.async_call(
            "select", "select_option", {"entity_id": cool_id, "option": "strong"}, blocking=True
        )
        await asyncio.sleep(6)
        print("cooling after 'strong':", hass.states.get(cool_id).state)

        # Fuetterungsdauer in Minuten: auf 6 min stellen
        dur_id = "number.smart_dual_temp_feeder_feeding_duration_lid_opening"
        dur = hass.states.get(dur_id)
        print("feed duration:", dur.state if dur else None, dur.attributes.get("unit_of_measurement") if dur else None)
        await hass.services.async_call(
            "number", "set_value", {"entity_id": dur_id, "value": 6}, blocking=True
        )
        await asyncio.sleep(6)
        print("feed duration after set 6:", hass.states.get(dur_id).state)

        try:
            await hass.services.async_call(
                "rfeeder", "feed_now", {"feed_duration_seconds": 60}, blocking=True
            )
            first_device = next(iter(coordinator.data.get("devices", {})), None)
            print("feed_now service call: OK (compartment", coordinator.get_compartment(first_device), ")")
        except Exception as err:  # noqa: BLE001
            print("feed_now service call failed:", err)

        await asyncio.sleep(8)  # evtl. Live-Reports nach dem Befehl abwarten
        feeding = hass.states.get("binary_sensor.smart_dual_temp_feeder_feeding_in_progress")
        if feeding:
            print("feeding binary sensor:", feeding.state)
        return 0
    finally:
        await hass.async_stop()
        import shutil

        shutil.rmtree(CONFIG_DIR, ignore_errors=True)


sys.exit(asyncio.run(main()))
