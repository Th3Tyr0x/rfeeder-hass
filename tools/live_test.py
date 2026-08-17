
# Credentials kommen aus den Umgebungsvariablen RFEEDER_ACCOUNT / RFEEDER_PASSWORD.
#!/usr/bin/env python3
"""End-to-end smoke test of the rfeeder integration core against the live cloud.

Uses the ACTUAL integration modules (api.py, proto.py, mqtt_client.py) with
light Home Assistant shims. Run from the repository root:

    python3 tools/live_test.py [--feed]

--feed additionally triggers a real feeding (physical device action!).
"""

from __future__ import annotations

import asyncio
import os
import sys
import types
from pathlib import Path

# --- HA shims so the integration modules import standalone -------------------
HA = types.ModuleType("homeassistant")
HA_CONST = types.ModuleType("homeassistant.const")


class _Platform:
    SENSOR = "sensor"
    BINARY_SENSOR = "binary_sensor"
    SWITCH = "switch"
    NUMBER = "number"
    BUTTON = "button"
    TIME = "time"
    SELECT = "select"


HA_CONST.Platform = _Platform
sys.modules["homeassistant"] = HA
sys.modules["homeassistant.const"] = HA_CONST

# Paket-Stumpf, damit rfeeder.*-Submodule ohne __init__.py (voluptuous/HA) laden
_pkg = types.ModuleType("rfeeder")
_pkg.__path__ = [str(Path(__file__).resolve().parent.parent / "custom_components" / "rfeeder")]
sys.modules["rfeeder"] = _pkg

import aiohttp  # noqa: E402

from rfeeder import proto  # noqa: E402
from rfeeder.api import UcspClient  # noqa: E402
from rfeeder.const import (  # noqa: E402
    CONF_AREA,
    CONF_APP_ID,
    CONF_APP_SECRET,
    CONF_BASE_URL,
    CONF_CLIENT_ID,
    CONF_PRODUCT_KEY,
    CONF_PW_SALT,
    CONF_TIMESTAMP_URL,
    DEFAULT_APP_ID,
    DEFAULT_APP_SECRET,
    DEFAULT_AREA,
    DEFAULT_BASE_URL,
    DEFAULT_CLIENT_ID,
    DEFAULT_PRODUCT_KEY,
    DEFAULT_PW_SALT,
    DEFAULT_TIMESTAMP_URL,
)
from rfeeder.mqtt_client import UcspMqttClient  # noqa: E402

ACCOUNT = os.environ["RFEEDER_ACCOUNT"]
PASSWORD = os.environ["RFEEDER_PASSWORD"]


async def main() -> None:
    feed = "--feed" in sys.argv
    config = {
        CONF_AREA: DEFAULT_AREA,
        CONF_APP_ID: DEFAULT_APP_ID,
        CONF_APP_SECRET: DEFAULT_APP_SECRET,
        CONF_BASE_URL: DEFAULT_BASE_URL,
        CONF_CLIENT_ID: DEFAULT_CLIENT_ID,
        CONF_PRODUCT_KEY: DEFAULT_PRODUCT_KEY,
        CONF_PW_SALT: DEFAULT_PW_SALT,
        CONF_TIMESTAMP_URL: DEFAULT_TIMESTAMP_URL,
    }
    async with aiohttp.ClientSession() as session:
        client = UcspClient(session, account=ACCOUNT, password=PASSWORD, config=config)
        await client.async_login()
        print(f"LOGIN OK  userId={client.user_id}  token_exp={client.access_expire_at}")

        devices = await client.get_devices(DEFAULT_PRODUCT_KEY)
        print(f"DEVICES: {[(d.get('deviceId'), d.get('deviceName')) for d in devices]}")
        dev = devices[0]["deviceId"]

        shadow = await client.get_shadow(DEFAULT_PRODUCT_KEY, dev)
        print(f"SHADOW keys={len(shadow)}")
        for key in sorted(shadow, key=lambda k: [int(p) for p in k.split(":")]):
            print(f"  {key}: {shadow[key].get('value')}")

        online = await client.get_onoffline(DEFAULT_PRODUCT_KEY, dev)
        print(f"ONOFFLINE: {online}")

        import time

        now = int(time.time() * 1000)
        today = await client.get_feeding_records_today(DEFAULT_PRODUCT_KEY, dev, now - 86400000, now)
        print(f"TODAY: {today}")
        stats = await client.get_plate_statistics(DEFAULT_PRODUCT_KEY, dev, now - 86400000, now)
        print(f"STATS: {stats}")

        creds = await client.get_mqtt_credentials()
        print(f"MQTT CREDS: url={creds['mqttUrl']} user={creds['username']}")

    # --- MQTT live check (blocking client) -----------------------------------
    url = creds["mqttUrl"].replace("mqtts://", "")
    host, _, port = url.partition(":")
    mqtt = UcspMqttClient(host, int(port))
    mqtt.connect(
        client_id=f"ha-test-{client.user_id}",
        username=creds["username"],
        password=creds["password"],
    )
    print("MQTT CONNECT OK")
    base = f"/{DEFAULT_PRODUCT_KEY}/{dev}"
    for suffix in ("thing/property/post", "thing/event/post", "onoffline"):
        mqtt.subscribe(f"{base}/{suffix}")
    mqtt.subscribe(f"{base}/{client.user_id}/upload/reply")
    print("MQTT SUBSCRIBE OK, listening 15s for device reports...")

    if feed:
        import random

        arg = proto.encode_feed_options(
            tray_compartment_index=1,
            feed_duration_seconds=300,
            heat_before_feeding=False,
            expected_heating_temperature=24,
            operator=client.user_id,
        )
        payload = proto.encode_invoke_request(proto.ENDPOINT_MAIN, proto.CLUSTER_FEED, proto.SERVICE_FEED, arg)
        interaction = proto.encode_interaction(
            message_id=1,
            exchange_id=random.randint(1, 0xFFFF),
            source_node_id=str(client.user_id),
            destination_node_id=dev,
            timestamp_ms=int(time.time() * 1000),
            opcode=proto.OPCODE_INVOKE_REQUEST,
            payload=payload,
        )
        mqtt.publish(f"{base}/{client.user_id}/thing/service", interaction, qos=1)
        print("FEED NOW sent!")

    import time as _t

    deadline = _t.monotonic() + 15
    while _t.monotonic() < deadline:
        message = mqtt.read_message(timeout=1.0)
        if message is None:
            continue
        topic, payload = message
        try:
            inter = proto.decode_interaction(payload)
            attrs, events = proto.decode_report_data(inter.payload)
            print(f"MQTT {topic}: opcode={inter.opcode} attrs={[(a.cluster, a.attribute, a.data.hex()) for a in attrs]} events={[(e.cluster, e.event, e.data.hex()) for e in events]}")
        except Exception as err:
            print(f"MQTT {topic}: raw={payload.hex()} ({err})")
    mqtt.close()
    print("DONE")


asyncio.run(main())
