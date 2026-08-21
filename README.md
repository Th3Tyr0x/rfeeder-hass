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
- Selects: *feed compartment* (tray 1–4) and *cooling level* (off/weak/medium/strong — same as the app)
- Switches: temperature control, pet detection, auto feeding, heat-before-feeding, automatic firmware updates
- Number: feeding duration (lid opening, in **minutes** — same as the app)

**Services (Actions)**
- `rfeeder.feed_now` — with optional compartment, duration (minutes), preheat parameters
- `rfeeder.add_schedule` / `rfeeder.remove_schedule` / `rfeeder.set_schedule_enabled` —
  manage feeding schedules stored **on the device** (run autonomously, even without HA)

## Feeding plans (weekly plan)

### Weekly plan blueprint (recommended)

[![Import Blueprint](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fraw.githubusercontent.com%2FTh3Tyr0x%2Frfeeder-hass%2Fmain%2Fblueprints%2Fautomation%2Frfeeder%2Fweekly_feeding_plan.yaml)

Click the badge (or in HA: *Settings → Automations & Scenes → Blueprints → Import Blueprint*)
and paste this URL:

```
https://raw.githubusercontent.com/Th3Tyr0x/rfeeder-hass/main/blueprints/automation/rfeeder/weekly_feeding_plan.yaml
```

One blueprint = the whole week: a time per tray compartment (compartments can be
disabled individually), the weekdays it applies to (empty = every day), lid-opening
duration and optional pre-heating. These plans run through Home Assistant and need
HA + cloud connectivity at feeding time.

### On-device schedules (run autonomously)

Schedules are stored **on the feeder itself** and keep working even when HA or
the internet is down. Besides the card you can manage them via actions:

```yaml
action: rfeeder.add_schedule
data:
  time: "08:00"              # local time (your HA timezone)
  weekdays: ["mon","tue","wed","thu","fri"]
  tray_compartment_index: 1
  feed_duration_minutes: 5
```

```yaml
action: rfeeder.sync_weekly_plan   # replace all schedules in one call
data:
  replace: true
  plan:
    - { compartment: 1, time: "08:00", weekdays: ["mon","tue","wed","thu","fri"] }
    - { compartment: 2, time: "12:30", weekdays: ["mon","wed","fri"] }
    - { compartment: 3, time: "18:00" }   # no weekdays = one-time
```

- `weekdays` empty = one-time; a raw bitmask int (bit0=Sunday … bit6=Saturday) is also accepted.
  Note: the device stores the time in **UTC** — the integration converts from your
  HA timezone for you (like the app, a fixed UTC time shifts one hour across DST changes).
- The sensor **Next scheduled feeding** lists all plans with their ids, local time and
  weekdays in its attributes.
- `rfeeder.remove_schedule` / `rfeeder.set_schedule_enabled` take the `schedule_id`.

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
