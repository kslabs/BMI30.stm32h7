# Host integration: independent relay control (BMI30 firmware 1.2.50+)

**Implemented on 2026-09-29:** Raspberry Pi websplit bundle `2026-09-29-1720`
(release `2026-09-29-171920`) is installed on S13, S11, S12 and M01,
including the inactive internal eMMC of USB-booted S13.
Group Mode now has Relay permission, minimum ON time, actual PC1 status and held Test/Stop.
The minimum starts when the relay activates. Continuous qualified detection keeps it ON
after that minimum; signal loss releases it once the minimum has elapsed.
Use the detector latch/accepted event, not the sound/LED/DetADC minimum-hold deadline.
The existing selected optical gate still applies. LED enable and sound mute do not control relay permission.
Implementation and test evidence: [Russian deployment report](RELAY_WEB_2026-09-29_RU.md),
[integration source](rpi_relay_web/relay_control.py), [engine tests](rpi_relay_web/test_engine_relay.py).

Core API: `GET /api/relay`, or `POST /api/command` with
`{"action":"relay","params":{"enabled":true,"duration_ms":1000,"persist":true}}`.
Use `test_enabled: true/false` for the held test. The authenticated portal forwards
`POST /api/relay` to that core API. Persistent settings are in `host/bmi30_relay.json`, keyed by USB serial;
held test state is never persisted. All EP0 and filesystem operations run outside the detector thread.

The following section describes the underlying STM32 protocol for other hosts.

Please add independent **Addressable LEDs enabled** and **Relay enabled** settings,
a **Relay duration (ms)** setting, and **Test relay / Stop** buttons.
The relay output is PC1, active HIGH.

Evaluate the existing detection/event condition, including the configured optical
sensor, before selecting outputs. Then dispatch each output independently:

```python
if event_qualifies_with_optical_condition:
    if addressable_leds_enabled:
        send_led_event(pattern_id, led_duration_ms)  # existing command 0x35
    if relay_enabled:
        relay_event(relay_duration_ms)              # new command 0x4B
```

Do not nest relay dispatch under the LED enable check. A disabled LED channel or
an OFF LED pattern must not suppress relay events. Firmware no longer derives
relay operation from LED_EVENT. Relay commands do not change the LEDs.
Firmware does not re-evaluate optical eligibility; the host owns that condition.

## USB protocol

Use the existing device handle and serialize commands with the app's USB command
queue. EP0 works while streaming; do not reset/reconfigure USB or stop ADC to send
these commands. All multi-byte values are little-endian.

| Opcode | Name | Payload, excluding opcode | Meaning |
|---|---|---|---|
| 0x4A | SET_RELAY_ENABLE | u8, exactly 0 or 1 | 0 immediately stops and disables; 1 permits future events/tests |
| 0x4B | RELAY_EVENT | u32 duration_ms | 0 immediately stops any relay mode; 1..0x7FFFFFFF starts/restarts a timed activation if enabled |
| 0x4C | GET_RELAY_STATUS | EP0 IN only | RLY1, 20 bytes |
| 0x4D | SET_RELAY_TEST | u8, exactly 0 or 1 | 1 holds ON if enabled; 0 stops immediately |

Recommended EP0 calls (PyUSB):

```python
import struct

def set_relay_enabled(enabled):
    dev.ctrl_transfer(0x40, 0x4A, int(bool(enabled)), 0, None, timeout=500)

def relay_event(duration_ms):
    if not 0 <= duration_ms <= 0x7FFFFFFF:
        raise ValueError("Relay duration is outside the supported range")
    dev.ctrl_transfer(0x40, 0x4B, 0, 0,
                      struct.pack("<I", duration_ms), timeout=500)

def set_relay_test(active):
    dev.ctrl_transfer(0x40, 0x4D, int(bool(active)), 0, None, timeout=500)

def get_relay_status():
    return bytes(dev.ctrl_transfer(0xC0, 0x4C, 0, 0, 20, timeout=500))
```

The no-data-stage form uses wValue for 0x4A/0x4D (0 or 1). Both also accept an
exactly one-byte data stage. For 0x4B the recommended data stage is exactly four
bytes; a no-data-stage shortcut accepts wValue=0..65535 ms. With a data stage,
wValue is ignored; send zero. Do not include the opcode in EP0 data.
Bulk OUT IF#2/EP 0x03 also accepts `[opcode | payload]` for 0x4A/0x4B/0x4D.
Invalid lengths/values cause EP0 STALL or are ignored on bulk. Bulk has no separate
ACK; read RLY1 to verify state. Do not consume the streaming IN endpoint for status.

Each accepted timed event restarts the relay timer from now using the supplied
duration. It does not add to the old deadline. Duration is independent of the LED
timer; values above 10 seconds are supported (e.g. 180000 ms = three minutes).
Disabled events are discarded and never replayed when permission is enabled.
Re-sending enable=1 is idempotent and does not interrupt an active event/test.

## Test button, stop and reconnect

- The relay enable switch must be ON for Test to work. Enabling alone does not
  energize the relay. Do not silently enable it as a side effect of Test.
- Test ON sends 0x4D=1, bypassing optical/detection conditions for this deliberate
  manual test. It holds until Test OFF (0x4D=0), RELAY_EVENT(0), disable, or reset.
- Held test replaces any timed event. Positive timed events during held test are
  ignored; Stop does not resume a previous event.
- To test for a chosen duration, use RELAY_EVENT(duration_ms) instead of held test.
- Switching Relay enabled OFF must immediately send 0x4A=0, cancelling all relay
  modes. Switching LEDs OFF sends the existing LED_EVENT(pattern=0) only.
- Permission is RAM-only and starts disabled. MCU reset and USB class
  deconfiguration/reset clear permission and turn PC1 OFF.
- On connect/reconnect, first validate RLY1 support, send disable, then apply the
  user's saved relay enable preference. Never automatically restart held test.
- On normal app shutdown send disable. Closing an app without USB deconfiguration
  is not a stop command; an unlimited held test can continue until an explicit stop.
- Older firmware does not support RLY1. Show the feature as unavailable; do not
  emulate independent relay control using LED_EVENT on firmware 1.2.49.

## RLY1 response layout (20 bytes)

| Offset | Type | Meaning |
|---|---|---|
| 0 | char[4] | RLY1 |
| 4 | u8 | Format version 1 |
| 5 | u8 | enabled: 0/1 |
| 6 | u8 | active: PC1 output register, 0/1 |
| 7 | u8 | pc1: actual GPIO input readback, 0/1 |
| 8 | u8 | mode: 0=off, 1=timed, 2=held test |
| 9 | u8[3] | Reserved, zero |
| 12 | u32 LE | remaining_ms; 0 when off, 0xFFFFFFFF for held test |
| 16 | u32 LE | max_duration_ms = 0x7FFFFFFF |

GPIO readback is not feedback from the relay's mechanical contacts.

## Acceptance checks

1. LEDs OFF, relay enabled: a qualifying event activates only the relay for the
   configured relay duration.
2. LEDs ON, relay disabled: the same event activates only the LEDs.
3. Both ON: each follows its own duration; stopping LEDs must not stop the relay.
4. Disabling the relay during a timed event or test immediately drives PC1 LOW.
5. A new timed event restarts its timer. Re-enabling does not replay old events.
6. Held test remains ON beyond the normal duration; Stop turns it OFF.
7. Reconnect/reset leaves output OFF until permission is restored and a new
   event or explicit Test command arrives.

Reference client: [vendor_relay.py](vendor_relay.py).
