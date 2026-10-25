---
name: plugin-tablet-kiosk
description: What the Tablet kiosk plugin records (a tablet that shows stimulus media and captures camera video, microphone audio and touches during records) and which tablet_kiosk__ tool to use. Read before answering questions about the tablet, its screen, touches or tablet captures of a record.
---

# Tablet kiosk plugin

A Linux tablet stands in or next to the arena. It shows stimulus media full screen (videos, images, or a
white / black / switched-off screen) and can record, at the same time:

- **camera video** (H.264, usually 640×480, segments of about 10 s);
- **audio**, three tracks: the microphone after echo cancellation, what the speaker played (reference),
  and the raw microphone;
- **touches** on its screen (every multitouch frame: time, finger id, x, y as 0–1 screen fractions,
  origin top left).

## When it records

- **With a record**: each experiment has a versioned *plan*. When it is enabled, the tablet starts a
  capture when a record of that experiment starts and stops when the record ends. A long record (or a
  tablet restart) gives several *parts* (`<run_id>.p1`, `.p2`, …). The plan a record used is fixed at the
  plugin's first sight of the record; `inferred: true` means the plugin saw it only after it started.
- **Manual**: someone started a one-off capture on the plugin's page (session `m-YYYYMMDD-HHMMSS`).

## Tools

- `tablet_kiosk__tablet_status()` — the tablet now: online, screen, volume, brightness, battery, the
  running capture.
- `tablet_kiosk__tablet_captures_of_run(run_id)` — the plan used and the captures of one record:
  times, why each ended (`stop_reason`), touch frame counts, size, `complete`.
- `tablet_kiosk__tablet_capture_detail(session_id)` — one capture: files, the screen log (what was on
  screen from when, with the media's original file names), volume and brightness.
- `tablet_kiosk__tablet_touches(session_id, limit?)` — the touches of one capture: count, touches while each
  screen content was shown (`per_screen`), and each touch (start, duration, start / end position, distance).
- `tablet_kiosk__tablet_plan_of_experiment(experiment_id)` — the current plan of an experiment.
- `tablet_kiosk__tablet_events(run_id?, limit?)` — the event log, newest first.

## Reading the numbers

- Times in captures and screen logs are the **tablet's clock** (Unix seconds); the status gives the
  tablet's clock offset to the host.
- `touches` counts contacts: one per finger put down (a tap and a long swipe are one touch each; two
  fingers at once are two). `touch_frames` counts raw multitouch frames (~40 per second while a finger
  moves): never report frames as touches. Who touched (animal, person) is not known to the plugin.
- Positions are fractions of the screen (x right, y down, 0-1); the screen is 16:10.
- `complete: false` means some files are still on the tablet (being fetched, or the host was low on
  disk): the numbers may grow.
- `stop_reason`: `record_ended` (normal for record captures), `duration` (manual captures, or a part
  reached 24 h), `lease_expired` (the plugin stopped renewing — host or plugin down — so the tablet
  stopped by itself), `interrupted` (tablet restart / power loss; the last ~10 s are lost), `user`.
- `no_data` / `no_plan` mean the plugin has nothing for that record / experiment; say so, do not guess.
- The plugin does not analyse the video or audio; it only knows what was shown and the touch counts.
