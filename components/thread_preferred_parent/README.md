# Thread Preferred Parent

ESPHome external component for experimenting with controlled Thread parent switching on Thread end devices.

It lets a Thread MTD / end device try to switch to a specific parent router, identified by either:

- `parent_extaddr` — preferred, stable IEEE 802.15.4 extended address.
- `parent_rloc` — RLOC16, useful for diagnostics but less stable.

> [!WARNING]
> This component is experimental. It patches ESP-IDF's vendored OpenThread source during the PlatformIO build. Use it for testing and diagnostics, not production parent-selection logic.

## What it does

The component uses a two-step process:

1. **Discovery / preflight**  
   Send an MLE Parent Request while staying attached to the current parent.

2. **Selected-parent attach**  
   If the configured parent responds, start a patched OpenThread attach attempt toward that selected parent.

Normal OpenThread behavior is unchanged while the component is idle. The patched behavior only runs when `request_switch()` is called.

## Example

```yaml
external_components:
  - source:
      type: git
      url: https://github.com/EwoutBergsma/ESPHome-Thread-ED-Switch-Parent
      ref: main
    components:
      - thread_preferred_parent
    refresh: 0s

thread_preferred_parent:
  id: preferred_parent

  # Preferred:
  parent_extaddr: "00124b0001abcdef"

  # Alternative:
  # parent_rloc: 0x5800

  max_attempts: 3
  retry_interval: 8s
  selected_attach_timeout: 16s

  parent_request_unicast: false
  early_attach_on_target: true
  early_attach_delay: 500ms

  require_selected_parent_hook: true
  log_parent_responses: true

button:
  - platform: template
    name: "Switch Thread Parent"
    on_press:
      - lambda: |-
          id(preferred_parent).request_switch();
```

## Runtime target controls

Optional Home Assistant text entities:

```yaml
text:
  - platform: template
    name: "Thread Preferred Parent ExtAddr"
    optimistic: true
    min_length: 0
    max_length: 23
    mode: text
    set_action:
      - lambda: |-
          id(preferred_parent).set_parent_extaddr(x);

  - platform: template
    name: "Thread Preferred Parent RLOC16"
    optimistic: true
    min_length: 0
    max_length: 6
    mode: text
    set_action:
      - lambda: |-
          id(preferred_parent).set_parent_rloc16(x);
```

## Options

| Option | Default | Description |
| --- | --- | --- |
| `id` | Required | Component ID used from lambdas. |
| `parent_extaddr` | Optional | Target parent extended address. Recommended. |
| `parent_rloc` | Optional | Target parent RLOC16. Do not use together with `parent_extaddr`. |
| `max_attempts` | `5` | Maximum discovery / attach attempts. |
| `retry_interval` | `8s` | Discovery window and retry delay. |
| `selected_attach_timeout` | `16s` | Time to wait for selected-parent attach to succeed. |
| `parent_request_unicast` | `false` | Send discovery Parent Request directly to the target ExtAddr instead of multicast. |
| `early_attach_on_target` | `true` | Start selected attach shortly after the target parent responds. |
| `early_attach_delay` | `250ms` | Delay before early attach after observing the target. |
| `require_selected_parent_hook` | `true` | Fail explicitly if the OpenThread patch hook is unavailable. |
| `log_parent_responses` | `true` | Log Parent Response diagnostics. |

## Accepted formats

```yaml
parent_extaddr: "00124b0001abcdef"
parent_extaddr: "00:12:4b:00:01:ab:cd:ef"
parent_extaddr: "00-12-4b-00-01-ab-cd-ef"
parent_extaddr: "0x00124b0001abcdef"

parent_rloc: 0x5800
parent_rloc: "5800"
```

## Companion diagnostics

Use this together with `thread_parent_info` to see which parent the device is currently attached to:

```yaml
external_components:
  - source:
      type: git
      url: https://github.com/EwoutBergsma/ESPHome-Thread-ED-Switch-Parent
      ref: main
    components:
      - thread_preferred_parent
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

## Logging

For normal lifecycle logs:

```yaml
logger:
  level: INFO
```

For live Parent Response rows:

```yaml
logger:
  level: VERY_VERBOSE
```

USB serial logging is recommended while testing, because the ESPHome API may briefly disconnect during selected-parent attach.

## Notes

- Prefer `parent_extaddr`; RLOC16 can change when Thread topology changes.
- The target parent must be visible during discovery.
- The component relies on patched OpenThread internals.
- Intended for Thread MTD / end-device configurations.
- FTD devices do not have a parent in the same end-device sense.