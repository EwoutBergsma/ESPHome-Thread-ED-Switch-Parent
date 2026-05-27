# Thread Ping with OpenThread reply RSS

ESPHome external component that sends one ICMPv6 ping to the current OpenThread parent of a Thread end device.

This drop-in version also patches ESP-IDF's vendored OpenThread ping sender so the built-in `otPingSenderReply` includes RSS from the received Echo Reply. The component then logs and can publish that RSS value as an ESPHome diagnostic sensor.

## What changed

The included `apply-openthread-ping-rss-patch.py` PlatformIO pre-build script patches OpenThread during the ESPHome build:

1. `include/openthread/ping_sender.h`
   - adds `int8_t mRss` to `otPingSenderReply`.
2. `src/core/utils/ping_sender.cpp`
   - fills `reply.mRss` using `aMessage.GetAverageRss()`.
3. `src/cli/cli_ping.cpp` or older `src/cli/cli.cpp`
   - updates the built-in OpenThread CLI ping output to print `rss=...` or `rss=unavailable`.

The ESPHome component registers this patch script automatically from `__init__.py` through PlatformIO `extra_scripts`.

## Example

```yaml
external_components:
  - source:
      type: local
      path: ./components
    components:
      - thread_ping

thread_ping:
  id: parent_pinger
  auto_start: false
  auto_interval: 60s
  timeout: 3000ms

  state:
    name: "Thread Parent Ping State"
  last_result:
    name: "Thread Parent Ping Result"
  target_rloc16:
    name: "Thread Parent Ping Target RLOC16"
  rtt:
    name: "Thread Parent Ping RTT"
  rss:
    name: "Thread Parent Ping RSS"

switch:
  - platform: thread_ping
    thread_ping_id: parent_pinger
    name: "Thread Parent Ping"
```

## Expected logs

After the patch, the component reply/result logs include RSS:

```text
[I][thread_ping:...]: Parent ping #4 reply: icmp_seq=17 rtt=26 ms size=16 rss=-50 dBm target=fd72:e9dd:7d27:e872:0:ff:fe00:7000
[I][thread_ping:...]: Parent ping #4 result: success; target ExtAddr=... RLOC16=7000 address=...; sent=1 received=1 loss=0% rtt=26 ms rss=-50 dBm elapsed=...
```

The OpenThread CLI `ping` line is also patched to include RSS:

```text
16 bytes from fd72:e9dd:7d27:e872:0:ff:fe00:7000: icmp_seq=17 hlim=64 time=26ms rss=-50
```

## Notes

- This is a local OpenThread API patch. The OpenThread header and compiled source must both be patched in the same ESP-IDF framework package.
- The patch script is idempotent and creates `.thread-ping-rss.bak` backups before modifying OpenThread files.
- The RSS value is the average RSS attached to the received OpenThread message, which is the same source used by OpenThread's MeshForwarder log line (`rss:-50.0`).
- If RSS is unavailable, the component publishes `NaN` to the `rss` sensor and logs `rss=unavailable`.
