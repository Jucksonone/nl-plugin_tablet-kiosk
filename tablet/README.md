# Tablet agent

Runs on the tablet (Xubuntu): a full-screen mpv player, camera + microphone capture (GStreamer), touch
recording (evdev), and the HTTP interface the Natural Land plugin `tablet_kiosk` uses
([`kiosk/server.py`](kiosk/server.py)). Plain system Python 3 (no virtualenv); system packages come from
`deploy/install.sh`.

## Install or update

From a machine that has this repository (replace the address):

```bash
TABLET=lilab@192.168.31.x
ssh $TABLET 'mkdir -p ~/kiosk/device'
rsync -a --delete --exclude __pycache__ tablet/ $TABLET:~/kiosk/device/
ssh -t $TABLET 'cd ~/kiosk/device && sh deploy/install.sh'      # first time only (sudo)
```

Then on the tablet:

1. Put the plugin's token into `~/.config/kiosk/config.toml` (the plugin's settings page shows the two
   lines to copy: `token`, `listen_port`).
2. `kiosk-mode on` (kiosk session, no desktop pop-ups) or, while setting up,
   `systemctl --user restart kiosk-agent` inside the normal desktop session.
3. `hostname -I` gives the address to enter on the plugin's settings page.

An update is the `rsync` line followed by `systemctl --user restart kiosk-agent` (a running capture is
finished properly first: the service gives it 30 s).

## Files

- `kiosk/agent.py` — commands, captures with a lease, the main loop (lease / duration watchdog, player check);
- `kiosk/server.py` — the HTTP interface (token in `Authorization: Bearer`);
- `kiosk/events.py` — numbered event log kept on disk (`~/.local/share/kiosk/events.jsonl`);
- `kiosk/spool.py` — finished capture files waiting for the plugin (`~/.local/share/kiosk/spool/`);
- `kiosk/cache.py` — stimulus media pushed by the plugin (`~/.local/share/kiosk/media/`, 20 GB, oldest unused evicted);
- `kiosk/player.py`, `capture.py`, `touch.py`, `system.py` — unchanged from the standalone version
  except that finished files are no longer uploaded;
- `deploy/` — packages, kiosk X session, systemd user service, PipeWire echo cancellation, power key, backlight rule.

The plugin's tests run this agent with a fake player and camera (`tests/tablet_fakes.py`).
