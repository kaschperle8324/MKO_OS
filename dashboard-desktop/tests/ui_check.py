"""Rendert die Anzeige gegen den HA-Nachbau (tests/mock_ha.py) und speichert Bildschirmfotos.

python3 tests/mock_ha.py 8123 &
QT_QPA_PLATFORM=offscreen MKO_HA_URL=http://127.0.0.1:8123 MKO_HA_TOKEN=testtoken python3 tests/ui_check.py
"""

import json
import sys
import urllib.request

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

sys.path.insert(0, ".")
from mko_dashboard import config as C  # noqa: E402
from mko_dashboard.ha import HAClient  # noqa: E402
from mko_dashboard.view import Dashboard  # noqa: E402

app = QApplication([])
view = Dashboard(C.load_layout())
client = HAClient("http://127.0.0.1:8123", "testtoken", view.entity_ids())
client.changed.connect(lambda: view.set_states(client.states))
client.history_ready.connect(view.set_history)
client.connection.connect(lambda ok, t: view.set_message(t))
client.open()
log = []


def shot1():
    log.append(f"verbunden={client.connected} werte={len(client.states)} verlauf={[len(s.points) for s in view.series.values()]}")
    view.resize(1024, 768)
    view.grab().save("/tmp/dash_1024x768.png")
    view.resize(1600, 1200)
    view.grab().save("/tmp/dash_1600x1200.png")
    # Warnung testen: Druck niedrig, Netz Einspeisung
    for eid, st in (("sensor.heizung_system_water_pressure", "0.9"), ("sensor.stromleser_e320_power_in_ha", "-2400")):
        req = urllib.request.Request("http://127.0.0.1:8123/test/set", data=json.dumps({"entity_id": eid, "state": st}).encode(),
                                     headers={"Authorization": "Bearer testtoken", "Content-Type": "application/json"})
        urllib.request.urlopen(req).read()
    QTimer.singleShot(1500, shot2)


def shot2():
    view.resize(1024, 768)
    view.grab().save("/tmp/dash_warnung.png")
    log.append(f"druck={client.states.get('sensor.heizung_system_water_pressure')} netz={client.states.get('sensor.stromleser_e320_power_in_ha')}")
    app.quit()


QTimer.singleShot(3500, shot1)
app.exec()
print("\n".join(log))
