#!/bin/sh
# X session for kiosk mode: the agent's full-screen player and nothing else.
# No desktop, panel, notification daemon or update notifier is started, so no
# system pop-up can appear over the player. Selected by `kiosk-mode on`.

SCREEN_MODE="${KIOSK_SCREEN_MODE:-1920x1200}"

xset s off -dpms
xset s noblank
xrandr --output eDP-1 --mode "$SCREEN_MODE" 2>/dev/null

# Notifications are D-Bus activated; hold their bus name so none can pop up.
pkill -u "$(id -u)" -x xfce4-notifyd 2>/dev/null
/usr/bin/python3 "$(dirname "$0")/null-notifyd.py" &

# The agent runs as a systemd user service; hand it this X display.
systemctl --user import-environment DISPLAY XAUTHORITY
dbus-update-activation-environment --systemd DISPLAY XAUTHORITY 2>/dev/null
systemctl --user restart kiosk-agent.service

# Minimal window manager: every window full screen, no title bars, no cursor.
# The session lasts as long as it runs; the agent goes down with the session so
# it never keeps drawing (or holding the GPU) while X is gone.
matchbox-window-manager -use_titlebar no -use_cursor no
systemctl --user stop kiosk-agent.service
