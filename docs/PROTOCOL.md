# Robotail RFeeder — UCSP Cloud Protocol

Reverse-engineered from the "R Feeder" Android app (`com.robotail.feeder.overseas`, v1.1.4)
via dynamic traffic capture (Frida/emulator) and full static analysis of the decrypted
Dart AOT snapshot (blutter). All request samples verified against the live cloud.

## 1. HTTP API

### Hosts
| Host | Purpose |
|---|---|
| `https://ucsp-oversea.ubtrobot.com` | Account, devices, shadow, feeder records, MQTT login |
| `https://apis.ubtrobot.com` | Server timestamp (`/v1/client-auth-service/api/timestamp`) |
| `mqtts://ucsp-eu.ubtrobot.com:20000` | MQTT broker (TLS) |

> **⚠ Backend inconsistency:** `ucsp-oversea.ubtrobot.com` is DNS round-robin between at least
> two backends (`8.209.85.152` OK / `47.236.85.245` does **not** know EU accounts → login fails
> with code 20009 "用户账号不存在"). Clients must retry login a few times on 20009 (each attempt
> has ~50% chance of hitting the good backend) or pin a working IP.

### Request signing
Every request carries 4 headers:

```
X-UBT-AppId:    26010001
X-UBT-ClientId: UE1A.230829.050
X-UBT-Language: en
X-UBT-Sign:     md5(timestampMs + SECRET + nonce + clientId) + " " + timestampMs + " " + nonce + " v2"
```

- `SECRET = ee6088ffd55c4f048046626833946eb1` (embedded in the app)
- `nonce` = 9 chars, `hex8-hex1` (e.g. `2cbfcfdb-8`)
- `timestampMs` = 13-digit Unix ms (device time; app fetches server time first)
- Authenticated requests additionally send `Authorization: <accessToken>`.

### Login
```
POST /usercenter/v1/users/login/password
{"account": "<email>", "accountType": 2, "areaCode": null,
 "password": md5(plainPassword + "9cb06cd103064100b86d67d412e844f0"), "area": "DE"}
```
Response:
```json
{"code":0,"data":{
  "tokenDTO":{"accessToken":{"token":"...","expireAt":1786918079},
              "refreshToken":{"token":"...","expireAt":1789509479}},
  "userDTO":{"userId":12345,"nickname":"U-XXXX","area":"DE","email":"u***@example.com"}}}
```
Token refresh: `POST /usercenter/v1/users/refresh-token`.

### Device & state endpoints (all `GET`, `Authorization` header)
| Path | Notes |
|---|---|
| `/platform/v1/device-relation/user/devices?productKey=41d9698a795` | device list (productKey, deviceId, deviceName) |
| `/device-shadow/v1/device/shadow?deviceId=..&productKey=..` | full device state (see §3) |
| `/device-manager/v1/device/onoffline?deviceIds=..&productKey=..` | `[{deviceId, onoffline: 0/1, eventTime}]` |
| `/feeder/v1/feeding-records?..&startTime=..&endTime=..&pageNum=1&pageSize=20` | feeding history incl. events |
| `/feeder/v1/feeding-records/today?..&startTime&endTime` | `{"feedingCount": N}` |
| `/feeder/v1/feeding-records/plate/statistics?..` | per-plate feeding counts |
| `/feeder/v1/feeding-records/unread/count?..` | unread count |
| `/feeder/v1/plate/picture-list?..` | plate photos |
| `/device-transport/mqtt/v1/login` | MQTT credentials (see §2) |
| `/ota/v1/version/alternative-upgradable` | firmware updates |

## 2. MQTT

- Login: `GET /device-transport/mqtt/v1/login` → `{"mqttUrl":"mqtts://ucsp-eu.ubtrobot.com:20000","username":"<appId>&<userId>&2","password":"<one-time, 16 chars>"}`
  The password is single-use-ish; fetch fresh credentials for every (re)connect.
- CONNECT: clientId `android-<userId>` (any unique id works), keepalive 60 s, clean session.
- Topics (pk = productKey `41d9698a795`, dev = deviceId e.g. `FAV008UBTF0000XXX`, uid = userId):

| Topic | Direction | Content |
|---|---|---|
| `/{pk}/{dev}/thing/property/post` | device → cloud/app | ReportData (attribute reports) |
| `/{pk}/{dev}/thing/event/post` | device → cloud/app | ReportData (event reports) |
| `/{pk}/{dev}/onoffline` | device → cloud/app | online/offline |
| `/{pk}/{dev}/{uid}/thing/service` | app → device | InvokeRequest (commands) |
| `/{pk}/{dev}/{uid}/thing/property/set` | app → device | WriteRequest (writable attributes) |
| `/{pk}/{dev}/{uid}/upload/reply` | device → app | replies to service/set calls |

### Wire format (protobuf)
Outer envelope `Interaction`:
```
1: modelVersion (varint, =25)      5: destinationNodeId (string = deviceId)
2: messageId (varint, counter)     6: timestamp (varint, ms)
3: exchangeId (varint, 0..65535!)  7: opcode (enum Opcode)
4: sourceNodeId (string = userId)  8: payload (bytes)
```

> **⚠ exchangeId must be 16-bit (0–65535).** The app generates it via
> `Random.nextInt(0x10000)`. Messages with larger exchangeIds are **silently
> dropped** by the device/broker (no reply, no effect) — verified empirically.
Opcode: `0=STATUS_RESPONSE 1=REPORT_DATA 2=WRITE_REQUEST 3=WRITE_RESPONSE 4=INVOKE_REQUEST 5=INVOKE_RESPONSE 6=READ_REQUEST 7=READ_RESPONSE`

Payloads:
- `InvokeRequest { 1: CommandPath{1:endpoint, 2:cluster, 3:command}, 2: arg (bytes) }`
- `WriteRequest { 1: repeated Attribute { 1: AttributePath{1:endpoint,2:cluster,3:attribute}, 2: data (bytes) } }`
- `ReportData  { 1: repeated Attribute attributeReports, 2: repeated Event eventReports }`
- `Event { 1: EventPath{1:endpoint,2:cluster,3:event}, 2: timestamp, 3: data }`

Attribute `data` encoding (verified against the live device):
- **Primitives/enums/bools**: one-field protobuf (`08 <value>` = field 1 varint).
- **Messages** (FeedPreferences, schedule lists): the **raw message bytes** directly
  (no wrapper). A field-1-wrapped message is accepted (status 0) but silently ignored!
- **Exactly ONE attribute per WriteRequest** — the firmware only applies the first
  attribute of a multi-attribute WriteRequest (the app sends separate publishes too).

**Temperature semantics (verified empirically against the live device):**
- `expectedCoolingTemperature` (attr 11:12): wire **and REST shadow** in
  half-degrees (app levels: weak=20, medium=17, strong=14 → 10/8.5/7 °C).
- Actual/tray/thermoelectric temperatures (attrs 11:11, 11:19, 11:20): plain °C
  everywhere.
- Temperature fields inside *messages* (FeedOptions, FeedPreferences,
  FeedingStartEvent): wire = °C × 2; REST (shadow/records) = °C.
Plain ints (durations, minutes, counts) are never scaled.

### Feed-Now example (captured & verified)
Topic `/{pk}/{dev}/{uid}/thing/service`:
```
Interaction{ modelVersion:25, messageId:28, exchangeId:64809, sourceNodeId:"<USER_ID>",
  destinationNodeId:"FAV008UBTF0000XXX", timestamp:…, opcode:4 (INVOKE_REQUEST),
  payload: InvokeRequest{ path:{10,11,2 (feed)}, arg: FeedOptions{
      trayCompartmentIndex:1, feedDurationSeconds:300, heatBeforeFeeding:false,
      expectedHeatingTemperature:48 (=24°C×2), operator:<USER_ID> } } }
```

## 3. Device shadow (thing model)

`GET /device-shadow/v1/device/shadow` returns `data: { "<endpoint>:<cluster>:<attr>": {time, value}, ... }`.
Endpoint is always `10`. REST values are already converted to plain units (°C, seconds).

### Cluster 10 — DeviceCluster
| Attr | Name | Type | W |
|---|---|---|---|
| 1 | firmwareVersion | {name, code} | |
| 2 | detectedFirmwareVersion | {name, code} | |
| 3 | firmwareDownloadState | TransferState | |
| 4 | firmwareDownloadProgress | int | |
| 5 | automaticUpdateEnabled | bool | ✓ |
| 6 | firmwareUpdateState | UpdateState | |
| 7 | powerSupply | PowerSupply (0=BATTERY,1=DC) | |
| 8 | batteryLevel | int | |
| 9 | wifiInfo | {ssid, security, standard, frequency} | |
| 10 | wifiRssi | int | |

### Cluster 11 — FeedCluster
| Attr | Name | Type | W |
|---|---|---|---|
| 1 | globalState | GlobalState | |
| 2 | feedState | FeedState | |
| 3 | doorState | DoorState | |
| 4 | trayState | TrayState | |
| 5 | trayCompartmentCount | int (4) | |
| 6 | trayFeedableCompartment | int | |
| 7 | preferences | FeedPreferences {feedDurationSeconds, heatBeforeFeeding, expectedHeatingTemperature} | ✓ |
| 8 | petDetectionOn | bool | ✓ |
| 9 | petDetected | bool | |
| 10 | temperatureControlOn | bool | ✓ |
| 11 | actualTemperature | int (°C) | |
| 12 | expectedCoolingTemperature | int (°C) | ✓ |
| 13 | feedingSchedules | FeedingScheduleList | |
| 14 | executingFeedingSchedule | FeedingScheduleList | |
| 15 | cancelledFeedingSchedule | CanceledFeedingScheduleList | |
| 16 | temperatureSensorFault | TemperatureSensorFault | |
| 17 | isAutoFeedingModeEnabled | bool | ✓ |
| 18 | temperatureTrend | TemperatureTrend | |
| 19 | thermoelectricTemperature | int (°C) | |
| 20 | trayTemperature | int (°C) | |
| 21 | latestExecutedFeedingSchedules | ExecutedFeedingScheduleList | |
| 22 | heatBeforeFeedNow | bool | ✓ |
| 23 | preheatDurationMinutes | int | |

### Cluster 12 — DiagnosisCluster
1 logUploadState, 2 logUploadProgress, 3 logStreamingEnabled ✓, 4 logLevel ✓

### Cluster 13 — TestingCluster
1 sn ✓, 2 productSecret ✓, 3 testCaseStatus, 4 factoryAgingTestResults

### Services (InvokeRequest, cluster 11)
| Cmd | Name | Argument |
|---|---|---|
| 2 | feed | FeedOptions |
| 3 | skipHeating | — |
| 4 | stopFeeding | — |
| 7 | addFeedingSchedules | FeedingScheduleList |
| 8 | removeFeedingSchedules | UInt8List {1: repeated uint32 values (packed)} = schedule IDs |
| 9 | updateFeedingSchedules | FeedingScheduleList |

### Feeder messages
```
FeedOptions       { 1:trayCompartmentIndex, 2:feedDurationSeconds, 3:heatBeforeFeeding(bool),
                    4:expectedHeatingTemperature(×2 on wire), 5:operator }
FeedingSchedule   { 1:id, 2:start(Time), 3:enabled(bool), 4:weekdays(bitmask), 5:feedOptions }
FeedingScheduleList        { 1: repeated FeedingSchedule schedules }
CanceledFeedingScheduleList{ 1: repeated canceledSchedules { 1:schedule, 2:cancelledAt, 3:cause } }
ExecutedFeedingScheduleList{ 1: repeated executedSchedules { 1:schedule, 2:executedAt } }
Time              { 1:hour, 2:minute }                    # UTC!
FeedPreferences   { 1:feedDurationSeconds, 2:heatBeforeFeeding, 3:expectedHeatingTemperature }
FeedingStartEvent { 1:startTime, 2:taskSource, 3:feedOptions, 4:expectedCoolingTemperature, 5:actualTemperature }
FeedingRecordEvent{ 1:startTime, 2:eventTime, 3:eventType, 4:faultCause, 5:isEnd }
UInt8List         { 1: repeated uint32 values (packed) }
WifiInfo          { 1:ssid, 2:security, 3:standard, 4:frequency }
FirmwareVersion   { 1:name, 2:code }
```
Schedule times are **UTC** (app converts local↔UTC). `weekdays` bitmask: bit0=Sun, bit1=Mon, … bit6=Sat.

### Enums
```
GlobalState:   0 RESETTING, 1 IDLE, 2 PAIRING, 3 FEEDING, 4 UPDATING, 5 TESTING, 6 ERASING
FeedState:     0 STOPPED, 1 PREPARING, 2 STARTING, 3 SERVING, 4 STOPPING, 5 FAULT
DoorState:     0 CLOSED, 1 OPENING, 2 OPENED, 3 CLOSING, 4 FAULT
TrayState:     0 NO_TRAY, 1 ALIGNED, 2 MISALIGNED, 3 ROTATING, 4 FAULT
PowerSupply:   0 BATTERY, 1 DC
UpdateState:   0 IDLE, 1 VALIDATING_FIRMWARE, 2 CHECK_PRECONDITION, 3 ABORTED, 4 UPDATING, 5 COMPLETED
TransferState: 0 IDLE, 1 TRANSFERRING, 2 STOPPED, 3 ABORTED, 4 COMPLETED
TemperatureTrend: 0 STABILIZING, 1 COOLING_DOWN, 2 HEATING_UP
TemperatureSensorFault: 0 NONE, 1 OPEN_CIRCUIT, 2 SHORT_CIRCUIT
FeedingActionSource: 0 PHYSICAL_BUTTON, 1 APP_REQUEST, 2 SCHEDULE, 3 AUTO_FEED
FeedingRecordEventType: 0 RESTART, 1 END, 2 INTERRUPTED, 3 FAULT, 4 POWER_SUPPLY_BATTERY,
  5 PREPARING, 6 STARTING, 7 DOOR_OPEN, 8 PET_DETECTED, 9 TRAY_ALIGNING, 10 DOOR_FAULT,
  11 TRAY_FAULT, 12 TIMEOUT
ScheduleCanceledCause: 0 ILLEGAL_STATE, 1 ALREADY_IN_FEEDING, 2 TRAY_MISSING
```

## 4. Quirks
- Backend DNS round-robin inconsistency (see §1) → the integration resolves the
  **regional host** via `GET /usercenter/v1/area-domains` (DE → `https://ucsp-eu.ubtrobot.com`)
  before login, which avoids the issue entirely. Login still retries on code 20009.
- Occasional spurious 401 code 10001 on authed endpoints → re-login and retry.
- `mqtt/v1/login` password is per-call; always fetch fresh credentials before reconnecting.
- MQTT `exchangeId` must be 16-bit (see §2).
- WriteRequests take effect immediately and produce: WRITE_RESPONSE on
  `.../upload/reply` (matching exchangeId) + a property/post report with the new value.
- InvokeRequests produce: INVOKE_RESPONSE on `.../upload/reply` + event/property reports.
- Device shows offline when it isn't always accurate; trust `onoffline` + recent shadow `time` together.
