#!/usr/bin/python3
"""Swallow desktop notifications in the kiosk session.

The notification daemon is D-Bus activated: the first notify call would start
it and its bubble would cover the player. Owning org.freedesktop.Notifications
first means notifications are accepted and silently dropped.
"""
import itertools

from gi.repository import Gio, GLib

XML = """
<node>
  <interface name="org.freedesktop.Notifications">
    <method name="GetCapabilities"><arg type="as" direction="out"/></method>
    <method name="Notify">
      <arg type="s" direction="in"/><arg type="u" direction="in"/><arg type="s" direction="in"/>
      <arg type="s" direction="in"/><arg type="s" direction="in"/><arg type="as" direction="in"/>
      <arg type="a{sv}" direction="in"/><arg type="i" direction="in"/>
      <arg type="u" direction="out"/>
    </method>
    <method name="CloseNotification"><arg type="u" direction="in"/></method>
    <method name="GetServerInformation">
      <arg type="s" direction="out"/><arg type="s" direction="out"/>
      <arg type="s" direction="out"/><arg type="s" direction="out"/>
    </method>
  </interface>
</node>
"""

ids = itertools.count(1)


def on_call(conn, sender, path, iface, method, params, invocation):
    if method == "GetCapabilities":
        invocation.return_value(GLib.Variant("(as)", ([],)))
    elif method == "Notify":
        invocation.return_value(GLib.Variant("(u)", (next(ids),)))
    elif method == "CloseNotification":
        invocation.return_value(None)
    elif method == "GetServerInformation":
        invocation.return_value(GLib.Variant("(ssss)", ("kiosk-null-notifyd", "kiosk", "1.0", "1.2")))


def on_bus(conn, name):
    node = Gio.DBusNodeInfo.new_for_xml(XML)
    conn.register_object("/org/freedesktop/Notifications", node.interfaces[0], on_call, None, None)


Gio.bus_own_name(Gio.BusType.SESSION, "org.freedesktop.Notifications",
                 Gio.BusNameOwnerFlags.REPLACE, on_bus, None, None)
GLib.MainLoop().run()
