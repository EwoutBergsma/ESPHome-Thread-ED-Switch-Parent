# ESPHome Thread ED Switch Parent

ESPHome external components for experimenting with controlled Thread end-device parent switching and parent diagnostics.

This repository contains two ESPHome external components:

- `thread_preferred_parent` — attempts to switch a Thread end device to a selected parent router.
- `thread_parent_info` — exposes the currently attached Thread parent as diagnostic ESPHome text sensors.

These components are mainly intended for testing, diagnostics, and controlled experiments with Thread parent selection behavior.

> [!WARNING]
> This project is experimental.
>
> The `thread_preferred_parent` component patches ESP-IDF's vendored OpenThread source during the PlatformIO build. Use it for testing and diagnostics, not as a general-purpose production Thread parent-selection mechanism.
>
> The `thread_parent_info` component is read-only: it only reports the current OpenThread parent and does not influence parent selection.

---

## Components

### `thread_preferred_parent`

The `thread_preferred_parent` component lets an ESPHome Thread end device attempt to connect to a specific Thread parent, identified either by the parent router's IEEE 802.15.4 extended address or by its RLOC16.

It uses a safer two-phase flow:

1. **Discovery / preflight**

   Send an MLE Parent Request while staying attached to the current parent.

   By default, this is a multicast Parent Request. When `parent_request_unicast: true` is enabled, the component sends the preflight Parent Request directly to the configured parent extended address instead of the all-routers multicast address.

2. **Selected-parent attach**

   Track every MLE Parent Response during discovery. If the configured target parent is observed, start selected-parent attach using the patched OpenThread hook.

   When `early_attach_on_target: true` is enabled, the component schedules selected-parent attach after `early_attach_delay` instead of waiting for the full `retry_interval`.

If the target is not observed, the component retries discovery without intentionally dropping the current Thread/API connection.

### `thread_parent_info`

The `thread_parent_info` component exposes the currently attached Thread parent as ESPHome diagnostic text sensors.

It can publish:

- `parent_extaddr` — the current parent's IEEE 802.15.4 extended address, formatted as 16 lowercase hexadecimal digits.
- `parent_rloc16` — the current parent's RLOC16, formatted as four lowercase hexadecimal digits.

This is especially useful together with `thread_preferred_parent`, because it lets you confirm which parent the end device actually attached to after a switch attempt.

Example:

```yaml
text_sensor:
  - platform: thread_parent_info
    update_interval: 30s
    parent_extaddr:
      name: "Parent Extended Address"
    parent_rloc16:
      name: "Parent RLOC16"
```

Possible diagnostic values include:

- a 16-character lowercase hexadecimal extended address, for example `00124b0001abcdef`;
- a 4-character lowercase hexadecimal RLOC16, for example `5800`;
- `no parent` when the OpenThread role is not `OT_DEVICE_ROLE_CHILD`;
- `parent unavailable` when `otThreadGetParentInfo()` fails;
- `OpenThread not enabled` if the component is compiled without `USE_OPENTHREAD`.

If the OpenThread instance lock cannot be acquired quickly, the component skips that update rather than replacing the last known state. This matches the behavior of returning `{}` from a template text-sensor lambda.

---

## Current version notes

### v20 busy guard and handoff diagnostics

Version v20 ignores a new `Switch Thread Parent` request while a switch is already active.

This protects the early-attach path from accidental double button presses in Home Assistant. It also adds one handoff diagnostic log before selected-parent attach starts, showing discovery elapsed time, buffered Parent Response count, and target-match count.

### v19 build fix

Version v19 fixes the early-attach OpenThread patch so `AttachToSelectedParent()` no longer declares an initialized local variable after a `VerifyOrExit(...)` macro.

This avoids the C++ compile error where `goto exit` crosses initialization of `threadPreferredParentDiscoveryActive`.

### v18 early attach

Version v18 adds:

- `early_attach_on_target`
- `early_attach_delay`

When the requested parent responds during discovery, the component can now schedule selected-parent attach after the configured debounce instead of waiting for the full `retry_interval`.

The OpenThread selected-parent hook also interrupts an active discovery-only pass so early attach does not bounce with `kErrorBusy`.

Discovery logs now include:

- target-observed time;
- total discovery time before attach.

### v16 build fix

Version v16 repairs the `parent_request_unicast` OpenThread patcher so `mle.cpp` is not truncated.

It also restores a previously truncated patched `mle.cpp` from the `.thread-preferred-parent.bak` backup when present, and always declares the unicast discovery bridge symbols before use.

### v15 unicast discovery option

Version v15 adds:

```yaml
parent_request_unicast: true
```

This sends the preflight Parent Request directly to the configured parent extended address instead of the all-routers multicast address.

The selected-parent attach path remains the same.

### v13 diagnostics cleanup

Version v13 cleans up Parent Response diagnostics:

- live Parent Response rows now use ESPHome's normal `VERY_VERBOSE` logger level;
- replay output is curated;
- discovery windows emit compact summaries;
- Parent Response timestamps are relative to the current attempt.

### v12 targeted attach update

Version v12 ports the important selected-parent attach lessons from `ESPHome-biparental-ED`.

It now:

- keeps the child attached while attempting the selected-parent Child ID exchange;
- pre-seeds the target extended address before `Attach(kSelectedParent)`;
- forces `ChildIdRequest` for selected-parent mode once the target Parent Response has populated the OpenThread parent candidate.

This is intended to fix the failure mode where the target appears in Parent Responses but OpenThread never completes the selected-parent attach.

### v10 behavior

Version v10 changes the selected-parent attach phase so OpenThread filters MLE Parent Responses.

After preflight discovery observes the requested extended address or RLOC16, OpenThread ignores non-target Parent Responses during the selected-parent attach.

This prevents the normal parent-selection heuristic from falling back to the old or strongest parent.

---

## Features

- Select a preferred Thread parent by `parent_extaddr` or `parent_rloc`.
  - `parent_extaddr` is recommended because it is more stable than RLOC16.
- Perform non-disruptive preflight discovery before attempting selected-parent attach.
- Send Parent Requests as multicast or unicast.
  - OpenThread normally uses multicast Parent Requests.
  - This component can also send unicast Parent Requests to reduce the number of possible Parent Responses.
- Start selected-parent attach shortly after the target responds with `early_attach_on_target: true`.
- Retry discovery when the target parent is not visible.
- Keep the node attached during the selected-parent Child ID exchange where possible.
- Filter non-target Parent Responses during selected-parent attach.
- Expose runtime controls through ESPHome lambdas, buttons, and text entities.
- Log Parent Response diagnostics for debugging Thread parent selection.
- Automatically register the OpenThread patch script as a PlatformIO pre-build script.
- Provide safeguards such as attach timeouts and a busy guard for repeated switch requests.
- Expose the current Thread parent through diagnostic text sensors.

---

## Parent switching process

In a typical Thread network, an end device is attached to exactly one parent router.

During a normal attach or parent-search process, the end device sends a multicast MLE Parent Request, receives Parent Responses from nearby routers or REEDs, lets the Thread stack evaluate the available candidates, and then attaches to the parent selected by the stack.

That selection is normally based on network and link-quality criteria such as RSSI-derived link quality, router connectivity, and child capacity.

In other words, the application cannot normally tell the Thread stack:

> Attach to this exact parent now.

The `thread_preferred_parent` component implements a more controlled process for parent switching.

Instead of immediately detaching or forcing a blind reattach, it first performs a discovery/preflight phase while the device remains attached to its current parent. During this phase, it sends an MLE Parent Request, records Parent Responses, and checks whether the configured target parent is in reach.

If the target parent is observed, the component starts a selected-parent attach using the patched OpenThread hook. This bypasses the normal candidate-selection step and attempts to attach specifically to the observed target parent, identified by extended address or RLOC16.

If the target is not observed, or if the selected-parent attach does not complete within the configured timeout, the component retries according to `max_attempts` and `retry_interval`.

The component does not continuously alter OpenThread's normal parent switching behavior while idle. Normal mechanisms such as periodic parent search, reattach after parent loss, and Child Supervision remain OpenThread-controlled.

The patched behavior is only intended to take effect during an explicit `request_switch()` operation.

---

## Requirements

- ESPHome with ESP-IDF framework support.
- An ESP32 Thread-capable target, such as an ESP32-H2 or ESP32-C6 board.
  - Testing has primarily been done on ESP32-C6.
- ESPHome `openthread:` enabled in the device configuration.
- The device must be configured as a Thread MTD / end device.
  - FTD devices do not have a parent in the same way an end device does.
- USB serial logging is recommended while testing, because the ESPHome API can temporarily disconnect during selected-parent attach.

---

## Repository layout

```text
components/
  thread_preferred_parent/
    __init__.py
    apply-openthread-selected-parent-hook.py
    thread_preferred_parent.cpp
    thread_preferred_parent.h

  thread_parent_info/
    README.md
    __init__.py
    text_sensor.py
    thread_parent_info.cpp
    thread_parent_info.h
```

---

## Example configuration

```yaml
esphome:
  name: thread-preferred-parent-test
  friendly_name: Thread Preferred Parent Test

esp32:
  board: esp32c6
  framework:
    type: esp-idf

logger:
  level: INFO
  # Use VERY_VERBOSE when you want live Parent Response rows.
  # level: VERY_VERBOSE

api:

ota:
  - platform: esphome

openthread:
  device_type: MTD
  tlv: "<PUT_YOUR_THREAD_DATASET_TLV_HERE>"

external_components:
  - source:
      type: git
      url: https://github.com/EwoutBergsma/ESPHome-Thread-ED-Switch-Parent
      ref: main
    components:
      - thread_preferred_parent
      - thread_parent_info
    refresh: 0s

thread_preferred_parent:
  id: preferred_parent

  # Preferred: target the parent by IEEE 802.15.4 extended address.
  parent_extaddr: "00124b0001abcdef"

  # Alternative: target the parent by RLOC16 instead.
  # Do not configure parent_extaddr and parent_rloc at the same time.
  # parent_rloc: 0x5800

  max_attempts: 3
  retry_interval: 8s
  selected_attach_timeout: 16s

  # Optional: send the preflight Parent Request directly to the target ExtAddr
  # instead of the all-routers multicast address.
  parent_request_unicast: false

  # Optional: start selected-parent attach shortly after the target responds,
  # instead of waiting for the full retry_interval discovery window.
  early_attach_on_target: true
  early_attach_delay: 250ms

  require_selected_parent_hook: true
  log_parent_responses: true

button:
  - platform: template
    name: "Switch Thread Parent"
    on_press:
      - lambda: |-
          id(preferred_parent).request_switch();

text:
  - platform: template
    id: preferred_parent_extaddr
    name: "Thread Preferred Parent ExtAddr"
    optimistic: true
    min_length: 0
    max_length: 23
    mode: text
    set_action:
      - lambda: |-
          id(preferred_parent).set_parent_extaddr(x);

  - platform: template
    id: preferred_parent_rloc16_text
    name: "Thread Preferred Parent RLOC16"
    optimistic: true
    min_length: 0
    max_length: 6
    mode: text
    set_action:
      - lambda: |-
          id(preferred_parent).set_parent_rloc16(x);

text_sensor:
  - platform: thread_parent_info
    update_interval: 30s
    parent_extaddr:
      name: "Parent Extended Address"
    parent_rloc16:
      name: "Parent RLOC16"
```

---

## `thread_preferred_parent` configuration options

| Option | Default | Description |
| --- | --- | --- |
| `id` | Required | ESPHome component ID. Use this ID from lambdas, template buttons, text entities, or other ESPHome actions, for example `id(preferred_parent).request_switch();`. |
| `parent_extaddr` | Optional | Target parent IEEE 802.15.4 extended address. This is the recommended way to identify a parent router because the extended address is stable across Thread topology changes. Configure either `parent_extaddr` or `parent_rloc`, not both. |
| `parent_rloc` | Optional | Target parent RLOC16, for example `0x5800`. This can be convenient while debugging because RLOC16 values appear in OpenThread diagnostics, but they can change when the Thread topology changes. Prefer `parent_extaddr` for repeated tests or long-lived configurations. Configure either `parent_rloc` or `parent_extaddr`, not both. |
| `max_attempts` | `5` | Maximum number of discovery cycles before the component gives up. |
| `retry_interval` | `8s` | Length of the discovery/preflight window and the delay before retrying discovery. |
| `selected_attach_timeout` | `16s` | Maximum time to wait after starting selected-parent attach for the device to become attached to the requested parent. |
| `parent_request_unicast` | `false` | When `false`, the preflight Parent Request is sent using normal multicast discovery. When `true`, the component tries to send the Parent Request directly to the target extended address. |
| `early_attach_on_target` | `true` | When enabled, the component starts selected-parent attach shortly after the target parent is first observed during discovery. |
| `early_attach_delay` | `250ms` | Delay between observing the target Parent Response and starting selected-parent attach when `early_attach_on_target` is enabled. Higher values, such as `500ms`, may be useful while testing. |
| `require_selected_parent_hook` | `true` | Require the patched OpenThread selected-parent attach hook to be available. Keeping this enabled makes failures explicit if the patch was not applied or is incompatible with the ESP-IDF/OpenThread version. |
| `log_parent_responses` | `true` | Enable buffered Parent Response diagnostics. With `logger.level: INFO` or `DEBUG`, the component reports lifecycle events, summaries, and relevant buffered responses. With `VERY_VERBOSE`, it also logs live Parent Response rows as they arrive. |

### Accepted `parent_extaddr` formats

```yaml
parent_extaddr: "00124b0001abcdef"
parent_extaddr: "00:12:4b:00:01:ab:cd:ef"
parent_extaddr: "00-12-4b-00-01-ab-cd-ef"
parent_extaddr: "0x00124b0001abcdef"
```

### Accepted `parent_rloc` formats

```yaml
parent_rloc: 0x5800
parent_rloc: "5800"
```

---

## `thread_parent_info` configuration options

The `thread_parent_info` platform is configured under `text_sensor:`.

| Option | Description |
| --- | --- |
| `parent_extaddr` | Optional text sensor exposing the current parent's IEEE 802.15.4 extended address. |
| `parent_rloc16` | Optional text sensor exposing the current parent's RLOC16. |
| `update_interval` | Optional polling interval. For diagnostics, `30s` is usually a reasonable starting point. |

Example:

```yaml
text_sensor:
  - platform: thread_parent_info
    update_interval: 30s
    parent_extaddr:
      name: "Parent Extended Address"
    parent_rloc16:
      name: "Parent RLOC16"
```

Runtime behavior:

- Publishes the current parent extended address when the device is attached as a Thread child.
- Publishes the current parent RLOC16 when the device is attached as a Thread child.
- Publishes `no parent` when the device is not currently attached as a child.
- Publishes `parent unavailable` when OpenThread cannot return parent info.
- Publishes `OpenThread not enabled` when compiled without OpenThread support.
- Skips the update if the OpenThread instance lock cannot be acquired quickly.

---

## Runtime control

You can change the target parent at runtime from ESPHome lambdas, template text entities, or Home Assistant controls:

```cpp
id(preferred_parent).set_parent_extaddr(x);
id(preferred_parent).set_parent_rloc16(x);
id(preferred_parent).request_switch();
```

A common setup is to expose:

- a button that calls `request_switch()`;
- a text entity for the target extended address;
- a text entity for the target RLOC16;
- diagnostic text sensors showing the currently attached parent.

The diagnostic sensors are useful for confirming whether the requested switch actually resulted in the desired parent.

---

## OpenThread patching

The `thread_preferred_parent` component automatically registers `apply-openthread-selected-parent-hook.py` as a PlatformIO pre-build script.

No manual `platformio_options.extra_scripts` entry is required.

The patch adds four hooks to ESP-IDF's vendored OpenThread source:

- `thread_preferred_parent_ot_register_parent_response_callback(...)`
- `thread_preferred_parent_ot_start_parent_discovery(...)`
- `thread_preferred_parent_ot_start_parent_discovery_unicast(...)`
- `thread_preferred_parent_ot_request_selected_parent_attach(...)`

The discovery hook starts `SearchForBetterParent()` but patches the MLE attacher so the discovery cycle is cancelled before Child ID Request.

This lets the component collect candidate Parent Responses without detaching from the current parent.

With `parent_request_unicast: true`, the same discovery-only path is used, but the Parent Request destination is the configured target extended address.

With `early_attach_on_target: true`, the selected-parent attach hook can also interrupt an active discovery-only pass once the target has been observed.

The `thread_parent_info` component does not perform selected-parent switching and does not add extra OpenThread patches. It only reads current parent information from OpenThread using the OpenThread APIs exposed through ESPHome's OpenThread integration.

---

## Expected logs

With normal `INFO` logging, discovery produces compact lifecycle and summary rows instead of replaying every Parent Response.

Example multicast discovery:

```text
Parent discovery attempt 1/5 for ExtAddr 32a4d516437f9abb
Starting non-disruptive multicast Parent Request discovery ...
Target Parent Response observed after 680 ms during discovery; early selected-parent attach scheduled in 250 ms
Discovery summary (early target debounce complete): 11 Parent Responses, 1 target match(es), best target RLOC16 0xb000 RSSI -69
Discovery result: target observed after 680 ms; starting selected-parent attach after 930 ms total discovery time (250 ms early-attach delay)
Preferred parent ExtAddr 32a4d516437f9abb was observed; starting selected-parent attach
Starting selected-parent attach to ExtAddr 32a4d516437f9abb
Selected-parent attach hook returned YES
Attach result: success after 3498 ms; ExtAddr 32a4d516437f9abb selected
Parent Response replay (success target replay): showing 2 buffered response(s)
Parent Response replay #4 attempt_t+680ms: ExtAddr 32a4d516437f9abb RLOC16 0xb000 RSSI -69 ... device_attached=YES target_match=YES
```

Example unicast discovery:

```text
Parent discovery attempt 1/5 for ExtAddr 32a4d516437f9abb
Starting non-disruptive unicast Parent Request discovery to ExtAddr 32a4d516437f9abb ...
Target Parent Response observed after 680 ms during discovery; early selected-parent attach scheduled in 250 ms
Discovery summary (early target debounce complete): 1 Parent Responses, 1 target match(es), best target RLOC16 0xb000 RSSI -69
Discovery result: target observed after 680 ms; starting selected-parent attach after 930 ms total discovery time (250 ms early-attach delay)
Preferred parent ExtAddr 32a4d516437f9abb was observed; starting selected-parent attach
Starting selected-parent attach to ExtAddr 32a4d516437f9abb
Selected-parent attach hook returned YES
Attach result: success after 3498 ms; ExtAddr 32a4d516437f9abb selected
```

Set the normal ESPHome logger to `VERY_VERBOSE` when you want every live Parent Response row:

```yaml
logger:
  level: VERY_VERBOSE
```

At `VERY_VERBOSE`, live rows look like this:

```text
Parent Response live #4 attempt_t+680ms: ExtAddr cec5115b300418f0 RLOC16 0xb000 RSSI -69 ... device_attached=YES target_match=YES
```

On failure, the final replay still shows all buffered candidates at `INFO`, because that is the useful forensic case.

During selected-parent attach, the ESPHome API may temporarily disconnect if the OpenThread stack drops or rebuilds the Thread route.

Version v12 tries to keep the node attached during the selected-parent Child ID exchange, but USB serial logs are still recommended for uninterrupted MLE diagnostics.

---

## Notes and limitations

- This repository is designed for experimentation with Thread parent selection and diagnostics.
- Prefer `parent_extaddr` when possible. RLOC16 values can be convenient for diagnostics, but extended addresses are a better stable identifier for a specific parent.
- RLOC16 values can change when Thread topology changes.
- The selected parent must be visible during discovery before the attach phase is started.
- If the OpenThread patch does not apply cleanly against the ESP-IDF/OpenThread version in your build, selected-parent switching will not work.
- Keep a serial console attached while developing or debugging, especially if the device's ESPHome API connection depends on Thread connectivity.
- `thread_parent_info` reports the current OpenThread parent; it does not force or influence parent selection.
- `thread_parent_info` is useful for confirming the result of a switch, but it does not prove why OpenThread selected a given parent.
- FTD devices do not have a Thread parent in the same end-device sense; these components are intended for Thread end devices / MTD configurations.

---

## Minimal diagnostics-only example

If you only want to expose the current Thread parent and do not need parent switching, you can load only `thread_parent_info`:

```yaml
external_components:
  - source:
      type: git
      url: https://github.com/EwoutBergsma/ESPHome-Thread-ED-Switch-Parent
      ref: main
    components:
      - thread_parent_info
    refresh: 0s

text_sensor:
  - platform: thread_parent_info
    update_interval: 30s
    parent_extaddr:
      name: "Parent Extended Address"
    parent_rloc16:
      name: "Parent RLOC16"
```