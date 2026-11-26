"""Fenster an den rechten Bildschirmrand setzen.

Unter X11 setzt das Programm die Position selbst. Unter Wayland dürfen
Programme das nicht; dort übernimmt eine KWin-Fensterregel (kwinrulesrc)
Position, Größe, "immer oben" und "nicht in der Taskleiste".
"""

from __future__ import annotations

import shutil
import subprocess

from PySide6.QtGui import QCursor, QGuiApplication

RULE_GROUP = "mko-claude"
_applied: tuple | None = None


def _kwrite(key: str, value: str, group: str = RULE_GROUP) -> None:
    subprocess.run(["kwriteconfig6", "--file", "kwinrulesrc", "--group", group, "--key", key, value], check=False)


def _kread(key: str, group: str) -> str:
    res = subprocess.run(["kreadconfig6", "--file", "kwinrulesrc", "--group", group, "--key", key],
                         capture_output=True, text=True, check=False)
    return res.stdout.strip()


def install_kwin_rule(x: int, y: int, w: int, h: int) -> bool:
    if not shutil.which("kwriteconfig6"):
        return False
    values = {
        "Description": "Claude-Seitenleiste (MKO OS)",
        "wmclass": "mko-claude", "wmclassmatch": "1", "wmclasscomplete": "false",
        "position": f"{x},{y}", "positionrule": "2",
        "size": f"{w},{h}", "sizerule": "2",
        "above": "true", "aboverule": "2",
        "skiptaskbar": "true", "skiptaskbarrule": "2",
        "skippager": "true", "skippagerrule": "2",
        "skipswitcher": "true", "skipswitcherrule": "2",
        "noborder": "true", "noborderrule": "2",
    }
    for key, value in values.items():
        _kwrite(key, value)
    rules = [r for r in _kread("rules", "General").split(",") if r]
    if RULE_GROUP not in rules:
        rules.append(RULE_GROUP)
        _kwrite("rules", ",".join(rules), "General")
        _kwrite("count", str(len(rules)), "General")
    for cmd in (["qdbus6", "org.kde.KWin", "/KWin", "reconfigure"],
                ["dbus-send", "--session", "--dest=org.kde.KWin", "--type=method_call", "/KWin",
                 "org.kde.KWin.reconfigure"]):
        if shutil.which(cmd[0]):
            subprocess.run(cmd, check=False, capture_output=True)
            break
    return True


def place(widget, width: int) -> None:
    global _applied
    screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
    geo = screen.availableGeometry()
    x, y, w, h = geo.x() + geo.width() - width, geo.y(), width, geo.height()
    if QGuiApplication.platformName().startswith("wayland"):
        if _applied != (x, y, w, h):
            install_kwin_rule(x, y, w, h)
            _applied = (x, y, w, h)
        widget.resize(w, h)
    else:
        widget.setGeometry(x, y, w, h)
