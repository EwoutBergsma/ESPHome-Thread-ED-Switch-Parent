# Thread Ping

ESPHome external component that sends one ICMPv6 ping to the current OpenThread parent of a Thread end device.

It uses OpenThread's ping sender API and targets the parent RLOC IPv6 address derived from the current parent's RLOC16.

## Behavior

Each ping attempt sends exactly **one** ICMPv6 Echo Request.

When a parent ping starts, the component:

1. checks that the device is currently a Thread child;
2. reads the current parent with `otThreadGetParentInfo()`;
3. derives the parent's RLOC IPv6 address from the mesh-local prefix and parent RLOC16;
4. sends one OpenThread ping;
5. checks the current parent again when ping statistics arrive.

If the parent changed while the ping was running, the result becomes:

```text
parent changed during ping
```

The ping statistics are still published, but they are marked as belonging to the parent that was current when the ping started.

## Example

```yaml
external_components:
  - source:
      type: git
      url: https://github.com/EwoutBergsma/ESPHome-Thread-ED-Switch-Parent
      ref: main
    components:
      - thread_ping
    refresh: 0s

thread_ping:
  id: parent_pinger
  auto_start: false
  auto_interval: 60s
  timeout: 3000ms

  state:
    name: "Thread Parent Ping State"
  last_result:
    name: "Thread Parent Ping Result"
  target_extaddr:
    name: "Thread Parent Ping Target ExtAddr"
  target_rloc16:
    name: "Thread Parent Ping Target RLOC16"
  target_address:
    name: "Thread Parent Ping Target Address"

  sent:
    name: "Thread Parent Ping Sent"
  received:
    name: "Thread Parent Ping Received"
  loss:
    name: "Thread Parent Ping Loss"
  rtt:
    name: "Thread Parent Ping RTT"

button:
  - platform: thread_ping
    thread_ping_id: parent_pinger
    name: "Thread Parent Ping Start/Stop"
```

Pressing the button toggles automatic pinging on and off.

With `auto_interval: 60s`, the component sends one ping, waits for the result or timeout, then waits 60 seconds before sending the next one.

## One-shot ping

You can also trigger a single ping from a template button:

```yaml
button:
  - platform: template
    name: "Ping Current Thread Parent Once"
    on_press:
      - lambda: |-
          id(parent_pinger).ping_parent_once();
```

## Options

| Option | Default | Description |
| --- | --- | --- |
| `id` | Required | Component ID. |
| `auto_start` | `false` | Start automatic parent pinging after boot. |
| `auto_interval` | `60s` | Delay between single-ping attempts. The next ping is scheduled after the previous result or timeout. |
| `timeout` | `3000ms` | Time to wait for the reply. Must be 65535ms or less. |
| `state` | Optional | Text sensor for `stopped`, `waiting`, or `pinging`. |
| `last_result` | Optional | Text sensor for the latest result. |
| `target_extaddr` | Optional | Text sensor showing the parent extended address targeted by the latest ping. |
| `target_rloc16` | Optional | Text sensor showing the parent RLOC16 targeted by the latest ping. |
| `target_address` | Optional | Text sensor showing the RLOC IPv6 address targeted by the latest ping. |
| `sent` | Optional | Sensor showing packets sent in the latest attempt, normally `1`. |
| `received` | Optional | Sensor showing replies received in the latest attempt, normally `0` or `1`. |
| `loss` | Optional | Sensor showing packet loss percentage, normally `0` or `100`. |
| `rtt` | Optional | Sensor showing round-trip time in ms. `0` when no reply was received. |

## Possible results

- `started` — automatic pinging was started.
- `stopped` — automatic pinging was stopped.
- `pinging` — a ping is active.
- `success` — the single request received a reply.
- `timeout` — no reply was received before `timeout`.
- `no parent` — the device was not a Thread child or no parent info was available when the ping started.
- `parent changed during ping` — the parent at the end of the ping did not match the parent targeted at the start.
- `busy` — OpenThread already had an active ping request.
- `failed to start` — OpenThread rejected the ping request for another reason.
- `OpenThread not enabled` — the firmware was built without OpenThread support.

## Notes

- The component only pings the current parent.
- Each attempt sends exactly one ping packet.
- It does not add a parent stability delay.
- It does not influence parent selection.
- It stops an in-flight ping if the start/stop button is pressed while pinging.
- The parent target is snapshotted at ping start, so results remain interpretable during parent switches.
