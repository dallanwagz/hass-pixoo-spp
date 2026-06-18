# Divoom Pixoo 16 → Home Assistant (over a Bluetooth-Classic SPP bridge)

A Home Assistant custom integration that drives a **Divoom Pixoo 16** locally — no cloud, no
Divoom app. The Pixoo speaks **Bluetooth Classic SPP**, which Home Assistant's BLE-only stack
can't reach, so this integration talks to it through an **[`untether_spp`](https://github.com/dallanwagz/untether/tree/main/components/untether_spp)**
ESP32 bridge: a classic ESP32 connects to the panel over RFCOMM and re-exposes the byte stream as
a TCP server. The integration opens that TCP socket and speaks the Pixoo wire protocol over it.

```
Home Assistant ──TCP──> ESP32 (untether_spp) ──Bluetooth Classic SPP──> Pixoo 16
   (this integration)        (transparent pipe)        (RFCOMM ch2)
```

Because the bridge is transparent, all protocol logic lives here in pure, unit-tested Python — the
same `protocol.py` you'd write for a BLE device; only the transport differs.

## What you get

- **Light** — the panel's global brightness (`0x74`), dimmable, with live read-back.
- **Sensor** — brightness as reported by the device (decoded from its `0x46` state echo).
- **Binary sensor** — whether the bridge currently holds the SPP link.
- **Services:**
  - `pixoo_spp.push_solid` — fill the panel with one RGB colour.
  - `pixoo_spp.push_image` — a 16×16 still from a palette + 256 row-major indices (`0x44`).
  - `pixoo_spp.upload_animation` — upload a clip that then **loops on-device** (`0x8b`, one shared
    ≤16-colour palette; the chunked START → ready-ACK → stream → resend dance is handled for you).

## Prerequisites

1. A **classic ESP32 (WROOM-32)** flashed with [`untether_spp`](https://github.com/dallanwagz/untether/tree/main/components/untether_spp),
   pointed at your Pixoo's MAC on RFCOMM `channel: 2`. Set `on_open_hex: "01 04 00 af 01 b4 00 02"`
   so the bridge fires the Pixoo's connect handshake itself.
2. The bridge reachable on your LAN (default TCP port `8888`). Verify before adding the integration:
   `printf '\x01\x04\x00\x74\x32\xaa\x00\x02' | nc <esp32-ip> 8888` should set brightness to 50%.

## Install

Copy `custom_components/pixoo_spp/` into your HA `config/custom_components/`, restart, then add
**Settings → Devices & Services → Add Integration → Divoom Pixoo (SPP bridge)** and enter the
bridge host + port. (Or add this repo to HACS as a custom repository.)

## Notes & limits

- **Single-bond:** the Pixoo accepts one host at a time. Keep the Divoom app (and any other
  controller) off it while the bridge holds the link.
- **One client per bridge:** `untether_spp` serves one TCP client at a time, so HA owns the socket.
- Animations cap at **16 colours**; >16 renders garbage on-device. Stills stream cleanly only with a
  **fixed palette/bpp** across frames (see the protocol notes in the
  [device profile](https://github.com/dallanwagz/untether/blob/main/examples/devices/divoom-pixoo-16.md)).
- This integration is the HA half; the full reverse-engineering write-up lives in the
  [untether](https://github.com/dallanwagz/untether) project.

## Development

```sh
pip install pytest pytest-homeassistant-custom-component ruff
pytest -q          # protocol golden frames + config-flow + setup/entity/service tests
ruff check custom_components/pixoo_spp
```
