#!/bin/sh
# Install the kiosk agent for the current user. Run on the tablet from ~/kiosk/device:
#   sh deploy/install.sh
# Needs sudo for packages, groups and the backlight udev rule.
set -eu
SUDO=${SUDO:-sudo}   # e.g. SUDO="sudo -A" with SUDO_ASKPASS for unattended installs
cd "$(dirname "$0")/.."
DEPLOY=deploy

$SUDO apt-get install -y \
    mpv python3-gi python3-evdev python3-websockets \
    gir1.2-gstreamer-1.0 gir1.2-gst-plugins-base-1.0 \
    gstreamer1.0-pipewire gstreamer1.0-gl gstreamer1.0-vaapi \
    gstreamer1.0-plugins-good gstreamer1.0-plugins-bad v4l-utils \
    matchbox-window-manager

# Backlight writable by the video group; touch device readable by the input group.
$SUDO install -m 644 $DEPLOY/90-kiosk-backlight.rules /etc/udev/rules.d/
$SUDO udevadm control --reload
$SUDO udevadm trigger --subsystem-match=backlight --action=add
$SUDO usermod -aG video,input "$USER"

mkdir -p ~/.config/kiosk ~/.config/pipewire/pipewire.conf.d ~/.config/systemd/user
[ -f ~/.config/kiosk/config.toml ] || cp $DEPLOY/config.example.toml ~/.config/kiosk/config.toml
cp $DEPLOY/60-echo-cancel.conf ~/.config/pipewire/pipewire.conf.d/
cp $DEPLOY/kiosk-agent.service ~/.config/systemd/user/

# Kiosk session: the agent is started by the session itself, not by the desktop,
# so the Xubuntu desktop stays usable for maintenance (`kiosk-mode off`).
rm -f ~/.config/autostart/kiosk-agent.desktop
chmod +x $DEPLOY/kiosk-session.sh
sed "s|@SESSION_SCRIPT@|$PWD/$DEPLOY/kiosk-session.sh|" $DEPLOY/kiosk-xsession.desktop > /tmp/kiosk.desktop
$SUDO install -m 644 /tmp/kiosk.desktop /usr/share/xsessions/kiosk.desktop
$SUDO install -m 755 $DEPLOY/kiosk-mode /usr/local/bin/kiosk-mode

# Power key: short press does nothing, hold ~5 s to power off; lid/suspend keys ignored.
$SUDO install -D -m 644 $DEPLOY/90-kiosk-logind.conf /etc/systemd/logind.conf.d/90-kiosk-logind.conf
$SUDO systemctl kill -s HUP systemd-logind

systemctl --user daemon-reload
systemctl --user restart pipewire pipewire-pulse wireplumber

echo
echo "Installed. Edit ~/.config/kiosk/config.toml (server_url, token), then run"
echo "  kiosk-mode on     # auto-login into the kiosk session (restarts the display)"
echo "  kiosk-mode off    # back to the Xubuntu desktop for maintenance"
