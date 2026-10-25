# nl-plugin_tablet-kiosk

Natural Land plugin "平板终端" (`tablet_kiosk`): a Linux tablet that shows stimulus media full screen and
records camera video, three microphone tracks and touches — during each record of an experiment, or by
hand. It is the standalone `tablet-kiosk` project (tablet agent + LAN server + web console) turned into a
plugin.

- **The plugin connects to the tablet** (plugin backends only listen on the host's loopback): the tablet
  agent in [`tablet/`](tablet/README.md) serves a small HTTP interface; the plugin pushes media to it,
  drives it and fetches the finished capture files.
- **Plans** per experiment: capture with every record, what to show, volume and brightness.
- **Captures** with the record they belong to: playback, screen log, touch counts, zip export.
- **Media library**, manual controls and manual captures on the system page.
- **Read-only Agent tools**.

User manual (Chinese): [`docs/使用手册.md`](docs/使用手册.md).

Development, verification and joining follow [`AGENTS.md`](AGENTS.md); this plugin's notes are at the end
of it, under "本插件说明".
