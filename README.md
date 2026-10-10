# Snapmaker Artisan for Home Assistant

A local-push Home Assistant integration for the Snapmaker Artisan 3D
printer, talking directly over its SACP protocol (TCP :8888) -- no
cloud, no Snapmaker account, no Luban required.

## Features

- Enter the printer's IP address; its name/model are auto-detected via
  UDP discovery, and device info (firmware version, modules) is pulled
  from the printer itself.
- Live status, elapsed print time, current print line.
- Live XYZ position and homed state (via a confirmed-working
  direct-query workaround -- the printer rejects the normal subscribe
  request for position).
- Extruder (both tools) and bed current/target temperatures.
- Fan speeds (3 channels).
- Enclosure: door open/closed, enclosure fan speed, door-detection
  enabled state for 3D-print and CNC jobs.
- A Connect/Disconnect button -- useful since the printer only accepts
  one SACP client at a time, so you can free the connection for Luban
  or another tool and reconnect when done.
- An adjustable update-interval number entity.

## Known limitations

- Progress %, remaining time, and total print line count are **not**
  retrievable on this hardware/firmware -- the query the printer would
  need to answer is never acknowledged at all. These entities exist but
  will show "unknown".
- Several raw byte fields in the enclosure and device-info responses
  are still unidentified; see the comments in `sacp.py` for exactly
  what's confirmed vs. guessed.

## Installation

### Via HACS (custom repository)

1. HACS -> the three-dot menu (top right) -> **Custom repositories**.
2. Add this repository's URL, category **Integration**.
3. Search for "Snapmaker Artisan" in HACS and install it.
4. Restart Home Assistant.
5. Settings -> Devices & Services -> Add Integration -> "Snapmaker
   Artisan" -> enter the printer's IP address.

### Manual

Copy `custom_components/snapmaker_artisan/` into your Home Assistant
`config/custom_components/` directory, restart, then add the
integration as above.

## Protocol notes

This was reverse-engineered through live traffic capture and
correlated testing (toggling things on the touchscreen while watching
which bytes changed) -- not from official documentation, which doesn't
exist publicly for SACP. Accuracy is believed good for everything
listed as "confirmed" in the code comments; anything else is a
best-effort guess and may be wrong or may stop working on a different
firmware version.
