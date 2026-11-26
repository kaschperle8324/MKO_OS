"""Fenster am Bildschirmrand verankern (X11 direkt, Wayland über eine KWin-Fensterregel)."""

from __future__ import annotations

import shutil
import subprocess

from PySide6.QtGui import QCursor, QGuiApplication

_applied: dict[str, tuple] = {}


def _kwrite(group: str, key: str, value: str):
    subprocess.run(["kwriteconfig6", "--file", "kwinrulesrc", "--group", group, "--key", key, value], check=False)


def install_kwin_rule(rule: str, wmclass: str, description: str, x: int, y: int, w: int, h: int) -> bool:
    if not shutil.which("kwriteconfig6"):
        return False
    values = {
        "Description": description, "wmclass": wmclass, "wmclassmatch": "1", "wmclasscomplete": "false",
        "position": f"{x},{y}", "positionrule": "2", "size": f"{w},{h}", "sizerule": "2",
        "above": "true", "aboverule": "2", "skiptaskbar": "true", "skiptaskbarrule": "2",
        "skippager": "true", "skippagerrule": "2", "skipswitcher": "true", "skipswitcherrule": "2",
        "noborder": "true", "noborderrule": "2",
    }
    for key, value in values.items():
        _kwrite(rule, key, value)
    res = subprocess.run(["kreadconfig6", "--file", "kwinrulesrc", "--group", "General", "--key", "rules"],
                         capture_output=True, text=True, check=False)
    rules = [r for r in res.stdout.strip().split(",") if r]
    if rule not in rules:
        rules.append(rule)
        _kwrite("General", "rules", ",".join(rules))
        _kwrite("General", "count", str(len(rules)))
    if shutil.which("qdbus6"):
        subprocess.run(["qdbus6", "org.kde.KWin", "/KWin", "reconfigure"], check=False, capture_output=True)
    return True


def place(widget, width: int, height: int | None = None, anchor: str = "right",
          rule: str = "mko-frigate", wmclass: str = "mko-frigate", description: str = "Kamera-Popup (MKO OS)"):
    screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
    g = screen.availableGeometry()
    h = height or g.height()
    h = min(h, g.height())
    x = g.x() + g.width() - width - (8 if anchor == "bottom-right" else 0)
    y = g.y() + g.height() - h - (8 if anchor == "bottom-right" else 0) if anchor == "bottom-right" else g.y()
    geo = (x, y, width, h)
    if QGuiApplication.platformName().startswith("wayland"):
        if _applied.get(rule) != geo:
            install_kwin_rule(rule, wmclass, description, *geo)
            _applied[rule] = geo
        widget.resize(width, h)
    else:
        widget.setGeometry(*geo)
