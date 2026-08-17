# Robotail RFeeder — Home Assistant Integration

[![hacs_custom](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/Th3Tyr0x/rfeeder-hass)

Custom integration for the **Robotail RFeeder** (Smart Dual-Temp Feeder, the app-controlled
wet-food pet feeder with cooling & pre-heating), talking directly to the vendor's UCSP cloud
(the same backend the "R Feeder" app uses).

Fully reverse-engineered from the app (dynamic traffic capture + static analysis of the
decrypted Dart AOT snapshot). Protocol documentation: [docs/PROTOCOL.md](docs/PROTOCOL.md).

## Features

**Sensors**
- Status (`IDLE`, `FEEDING`, …), feeding state, door state, tray state
- Temperatures: interior, tray, cooling element, temperature trend
- Feedings today (+ per-plate statistics), last feeding (timestamp + events)
- Next scheduled feeding (with the full schedule list as attributes)
- Diagnostic: power supply, battery, Wi-Fi SSID/signal, firmware, tray compartments

**Binary sensors**
- Online, feeding in progress, pet detected
- Faults: door, tray, feeding, temperature sensor

**Controls**
- Buttons: *Feed now*, *Stop feeding*, *Skip heating*
- Switches: temperature control, pet detection, auto feeding, heat-before-feeding, automatic firmware updates
- Numbers: cooling target temperature (°C), feeding duration, heating target temperature

**Services (Actions)**
- `rfeeder.feed_now` — with optional compartment, duration, preheat parameters
- `rfeeder.add_schedule` / `rfeeder.remove_schedule` / `rfeeder.set_schedule_enabled` —
  manage feeding schedules (times are UTC, `weekdays` bitmask bit0=Sunday … bit6=Saturday)

## How it works

- **REST polling** (every 60 s) of the device shadow, online status and feeding records
  via `https://ucsp-eu.ubtrobot.com` (regional host resolved automatically from your area code).
- **Persistent MQTT connection** (`ucsp-eu.ubtrobot.com:20000`, TLS) for live pushes
  (state changes within seconds) and all device commands.
- Request signing (`X-UBT-Sign` v2) and the thing-model protobuf wire format are
  re-implemented from scratch — see `docs/PROTOCOL.md`.

## Installation

### Via HACS (recommended)

1. In HACS, open the menu (⋮) → **Custom repositories**.
2. Add `https://github.com/Th3Tyr0x/rfeeder-hass` with category **Integration** → **Add**.
3. Search for **Robotail RFeeder** in HACS → **Download**.
4. Restart Home Assistant.
5. **Settings → Devices & Services → Add Integration** → search for **Robotail RFeeder**.
6. Enter the email/password of your R Feeder app account and your region code (e.g. `DE`).

### Manual

1. Copy `custom_components/rfeeder/` into your Home Assistant `custom_components/` directory.
2. Restart Home Assistant.
3. **Settings → Devices & Services → Add Integration** → search for **Robotail RFeeder**.
4. Enter the email/password of your R Feeder app account and your region code (e.g. `DE`).

## Notes & quirks

- The vendor cloud has inconsistent global DNS round-robin backends; the integration
  resolves your **regional** host (`area-domains`) before login, which avoids random
  login failures. EU accounts live on `ucsp-eu.ubtrobot.com`.
- MQTT `exchangeId`s must be 16-bit or messages are silently dropped (yes, really).
- The cooling target temperature is reported by the device in half-degrees; the app levels
  *weak/medium/strong* map to 10/8.5/7 °C. The integration shows plain °C.
- Schedule times are stored in **UTC** (like the app does).

## Credits

Protocol analysis building on the approach of
[robotail-hass](https://github.com/hermeneanc/robotail-hass) (Robotail cat litter box).
Not affiliated with Robotail/UBTech. Use at your own risk.
