# AGENTS.md — RFeeder HA Integration

## Project
Home Assistant custom integration for the **Robotail RFeeder** (Smart Dual-Temp Feeder)
against the UBTech UCSP cloud. Domain: `rfeeder`.

## Layout
- `custom_components/rfeeder/` — the integration
  - `api.py` — async REST client (sign v2, regional host resolution, token refresh)
  - `mqtt_client.py` — stdlib blocking MQTT 3.1.1-over-TLS client (no paho dependency)
  - `proto.py` — minimal protobuf + thing-model codecs (see docs/PROTOCOL.md)
  - `coordinator.py` — DataUpdateCoordinator (REST poll) + MQTT thread (live push, commands)
  - `sensor.py` / `binary_sensor.py` / `switch.py` / `number.py` / `select.py` / `button.py` — platforms
  - `secrets.py` — obfuscated bundle of the app constants (do not paste raw values elsewhere)
- `custom_components/rfeeder/www/rfeeder-card.js` — Lovelace weekly-plan card
  (grid: compartments × days; auto-registered via frontend.add_extra_js_url)
- `docs/PROTOCOL.md` — full reverse-engineered protocol documentation (READ THIS FIRST)
- `tools/live_test.py` — end-to-end smoke test against the live cloud
  (uses real integration modules with HA shims; `--feed` triggers a physical feeding!)
- `tools/ha_integration_test.py` — boots a real HA core and exercises the integration
  (needs a HA install, e.g. a venv with `pip install homeassistant`; credentials via
  RFEEDER_ACCOUNT / RFEEDER_PASSWORD env vars)

## Hard-won vendor quirks (do not regress!)
1. **Regional host**: resolve `GET /usercenter/v1/area-domains` before login and use the
   regional host (DE→`ucsp-eu.ubtrobot.com`). The global host has inconsistent DNS
   round-robin backends (~50% of requests fail with 20009/10001 otherwise).
2. **MQTT exchangeId must be 16-bit** (`Random.nextInt(0x10000)` in the app). Larger ids
   are silently dropped by the device/broker (no reply, no effect).
3. **Temperatures**: cooling target (attr 10:11:12) is half-degrees on the wire AND in the
   raw REST shadow (normalize to °C in the coordinator). Message fields
   (FeedOptions/FeedPreferences/FeedingStartEvent) are °C×2 on the wire, °C in REST.
   Actual/tray/thermoelectric temperatures are plain °C everywhere.
4. MQTT password from `/device-transport/mqtt/v1/login` is one-time — fetch fresh
   credentials on every reconnect.
5. Schedules are stored in UTC; `weekdays` bitmask bit0=Sunday … bit6=Saturday.
6. Attribute writes: exactly ONE attribute per WriteRequest; message-typed data
   (FeedPreferences) as raw message bytes, primitives as field-1 varint.

## Testing
- `python3 tools/live_test.py` — login, device list, shadow, records, MQTT connect/subscribe.
- `python3 tools/live_test.py --feed` — additionally sends Feed-Now (physical action!).
- There is a probe area in `/tmp/opencode` (throwaway): capture files, blutter workspace.

## Conventions
- No external dependencies (stdlib only); async REST via aiohttp; MQTT runs in a daemon
  thread and talks to HA via `hass.loop.call_soon_threadsafe`.
- Commands from HA go through the coordinator's command queue to the MQTT thread.
- Follow the existing code style (type hints, docstrings, `_LOGGER`).
