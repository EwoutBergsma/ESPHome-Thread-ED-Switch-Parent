# ESPHome Thread Parent Info external component

This external component exposes the current Thread parent as ESPHome diagnostic text sensors:

- `parent_extaddr`: current parent IEEE 802.15.4 extended address, formatted as 16 lowercase hex digits.
- `parent_rloc16`: current parent RLOC16, formatted as four lowercase hex digits.

It is intended for Thread end devices using ESPHome's `openthread:` component. It uses the same OpenThread calls as the working template lambda version:

- `otThreadGetDeviceRole(instance)`
- `otThreadGetParentInfo(instance, &parent)`

## Files to add to your repo

Copy this folder into your repository:

```text
components/thread_parent_info/
```

The resulting repository layout should include:

```text
components/
  thread_preferred_parent/
  thread_parent_info/
    __init__.py
    text_sensor.py
    thread_parent_info.cpp
    thread_parent_info.h
```

## YAML usage

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

## Runtime behavior

The component publishes:

- `no parent` when the OpenThread role is not `OT_DEVICE_ROLE_CHILD`.
- `parent unavailable` when `otThreadGetParentInfo()` fails.
- `OpenThread not enabled` if the component is compiled without `USE_OPENTHREAD`.

If the OpenThread instance lock cannot be acquired quickly, the component skips that update rather than replacing the last known state. This matches the behavior of returning `{}` from a template text-sensor lambda.
