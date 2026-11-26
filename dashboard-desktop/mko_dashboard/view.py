"""Die Anzeige: Kopfzeile mit Uhr und Außentemperatur, darunter Kacheln je Bereich."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from . import template as T

BG = QColor("#0e1013")
TILE = QColor("#181b20")
TEXT = QColor("#eef1f5")
DIM = QColor("#8a93a0")
ALERT = QColor("#ff5c5c")
WEEKDAYS = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
MONTHS = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober",
          "November", "Dezember"]


class Series:
    """Verlauf eines Ausdrucks in 10-Minuten-Schritten."""

    STEP = timedelta(minutes=10)

    def __init__(self, expr: str, hours: int):
        self.expr = expr
        self.hours = hours
        self.points: list[tuple[datetime, float]] = []

    def from_history(self, history: dict):
        ents = T.entities_in(self.expr)
        end = datetime.now(timezone.utc)
        start = end - timedelta(hours=self.hours)
        seq = {e: history.get(e, []) for e in ents}
        idx = {e: 0 for e in ents}
        cur: dict[str, str] = {}
        pts = []
        t = start
        while t <= end:
            for e in ents:
                s = seq[e]
                while idx[e] < len(s) and s[idx[e]][0] <= t:
                    cur[e] = s[idx[e]][1]
                    idx[e] += 1
            try:
                v = T.evaluate(self.expr, cur)
                if isinstance(v, (int, float)):
                    pts.append((t, float(v)))
            except (T.Missing, ValueError, ZeroDivisionError):
                pass
            t += self.STEP
        self.points = pts

    def live(self, states: dict):
        try:
            v = T.evaluate(self.expr, states)
        except (T.Missing, ValueError, ZeroDivisionError):
            return
        if not isinstance(v, (int, float)):
            return
        now = datetime.now(timezone.utc)
        if self.points and now - self.points[-1][0] < self.STEP:
            self.points[-1] = (self.points[-1][0], float(v))
        else:
            self.points.append((now, float(v)))
        cutoff = now - timedelta(hours=self.hours)
        self.points = [p for p in self.points if p[0] >= cutoff]


class Dashboard(QWidget):
    def __init__(self, layout: dict):
        super().__init__()
        self.setWindowTitle("Haus-Anzeige")
        self.layout_cfg = layout
        self.states: dict[str, str] = {}
        self.message = "Verbinde mit Home Assistant …"
        self.dim = 0.0
        self.shift = (0, 0)
        self.series: dict[tuple[int, int], Series] = {}
        for si, sec in enumerate(layout.get("section", [])):
            for ti, tile in enumerate(sec.get("tile", [])):
                if tile.get("spark"):
                    self.series[(si, ti)] = Series(tile["spark"], int(tile.get("spark_hours", 12)))
        self.setAttribute(Qt.WA_OpaquePaintEvent)
        self.clock = QTimer(self, interval=1000)
        self.clock.timeout.connect(self.update)
        self.clock.start()

    # ------------------------------------------------------------- Daten
    def entity_ids(self) -> set[str]:
        ids: set[str] = set()
        header = self.layout_cfg.get("header", {})
        for text in [header.get("temperature", "")] + list(header.get("chips", [])):
            ids |= T.entities_in_template(text)
        for sec in self.layout_cfg.get("section", []):
            for tile in sec.get("tile", []):
                for key in ("value", "sub"):
                    ids |= T.entities_in_template(tile.get(key, ""))
                for key in ("spark", "gauge", "alert"):
                    if tile.get(key):
                        ids |= T.entities_in(tile[key])
        return ids

    def set_states(self, states: dict):
        self.states = dict(states)
        for s in self.series.values():
            s.live(self.states)
        self.update()

    def set_history(self, history: dict):
        for s in self.series.values():
            s.from_history(history)
        for s in self.series.values():
            s.live(self.states)
        self.update()

    def set_message(self, text: str):
        self.message = text
        self.update()

    # ----------------------------------------------------------- Zeichnen
    def _font(self, px: float, bold: bool = False, light: bool = False) -> QFont:
        f = QFont(self.font())
        f.setPixelSize(max(8, int(px)))
        f.setWeight(QFont.DemiBold if bold else QFont.Light if light else QFont.Normal)
        return f

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), BG)
        w, h = self.width(), self.height()
        unit = min(w / 1024, h / 768)            # Maßstab: 1024×768 als Bezug
        m = 22 * unit
        p.translate(*self.shift)

        # Kopfzeile
        head_h = 96 * unit
        now = datetime.now()
        p.setPen(TEXT)
        p.setFont(self._font(64 * unit, light=True))
        p.drawText(QRectF(m, m * 0.6, w * 0.4, head_h), Qt.AlignLeft | Qt.AlignVCenter, now.strftime("%H:%M"))
        p.setPen(DIM)
        p.setFont(self._font(18 * unit))
        date = f"{WEEKDAYS[now.weekday()]}, {now.day}. {MONTHS[now.month - 1]}"
        p.drawText(QRectF(m + 190 * unit, m * 0.6, w * 0.4, head_h), Qt.AlignLeft | Qt.AlignVCenter, date)
        header = self.layout_cfg.get("header", {})
        temp = T.render(header.get("temperature", ""), self.states)
        p.setPen(TEXT)
        p.setFont(self._font(44 * unit, light=True))
        p.drawText(QRectF(w * 0.5, m * 0.6, w * 0.5 - m, head_h * 0.62), Qt.AlignRight | Qt.AlignVCenter, temp)
        chips = [T.render(c, self.states) for c in header.get("chips", [])]
        p.setFont(self._font(14 * unit))
        x = w - m
        for chip in reversed([c for c in chips if c and c != T.DASH]):
            tw = p.fontMetrics().horizontalAdvance(chip) + 20 * unit
            r = QRectF(x - tw, m * 0.6 + head_h * 0.64, tw, 24 * unit)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#22262d"))
            p.drawRoundedRect(r, 12 * unit, 12 * unit)
            p.setPen(DIM)
            p.drawText(r, Qt.AlignCenter, chip)
            x -= tw + 8 * unit

        # Bereiche
        sections = self.layout_cfg.get("section", [])
        top = m * 0.6 + head_h + 10 * unit
        title_h = 30 * unit
        gap = 12 * unit
        rows = []
        for sec in sections:
            cols = max(1, int(sec.get("columns", 3)))
            n = sum(int(t.get("span", 1)) for t in sec.get("tile", []))
            rows.append((sec, cols, max(1, -(-n // cols))))
        total_rows = sum(r for _, _, r in rows) or 1
        avail = h - top - m - len(rows) * title_h - (total_rows - 1) * gap - len(rows) * gap
        row_h = avail / total_rows
        y = top
        for si, (sec, cols, nrows) in enumerate(rows):
            p.setPen(DIM)
            p.setFont(self._font(15 * unit, bold=True))
            p.drawText(QRectF(m, y, w, title_h), Qt.AlignLeft | Qt.AlignVCenter, sec.get("title", "").upper())
            y += title_h
            col_w = (w - 2 * m - (cols - 1) * gap) / cols
            c = r = 0
            for ti, tile in enumerate(sec.get("tile", [])):
                span = min(cols, int(tile.get("span", 1)))
                if c + span > cols:
                    c, r = 0, r + 1
                rect = QRectF(m + c * (col_w + gap), y + r * (row_h + gap), col_w * span + gap * (span - 1), row_h)
                self._tile(p, rect, tile, self.series.get((si, ti)), unit)
                c += span
            y += nrows * row_h + (nrows - 1) * gap + gap

        # Statuszeile
        if self.message:
            p.setFont(self._font(14 * unit))
            r = QRectF(0, h - 30 * unit, w, 26 * unit)
            p.fillRect(r, QColor(120, 40, 40, 200))
            p.setPen(TEXT)
            p.drawText(r, Qt.AlignCenter, self.message)
        if self.dim > 0:
            p.resetTransform()
            p.fillRect(self.rect(), QColor(0, 0, 0, int(255 * self.dim)))
        p.end()

    def _tile(self, p: QPainter, rect: QRectF, tile: dict, series: Series | None, unit: float):
        accent = QColor(tile.get("color", "#5aa9e6"))
        alert = bool(tile.get("alert")) and T.condition(tile["alert"], self.states)
        p.setPen(QPen(ALERT, 2 * unit) if alert else Qt.NoPen)
        p.setBrush(TILE)
        p.drawRoundedRect(rect, 14 * unit, 14 * unit)
        pad = 16 * unit
        inner = rect.adjusted(pad, pad * 0.8, -pad, -pad * 0.7)

        # Verlauf im unteren Drittel
        if series and len(series.points) > 1:
            self._spark(p, QRectF(rect.left(), rect.bottom() - rect.height() * 0.30, rect.width(), rect.height() * 0.30),
                        series, accent, unit)

        p.setPen(ALERT if alert else accent)
        p.setFont(self._font(14 * unit, bold=True))
        p.drawText(QRectF(inner.left(), inner.top(), inner.width(), 20 * unit), Qt.AlignLeft | Qt.AlignVCenter,
                   tile.get("title", ""))
        badge = T.render(tile.get("badge", ""), self.states).strip()
        if badge and badge != T.DASH:
            p.setFont(self._font(12 * unit, bold=True))
            bw = p.fontMetrics().horizontalAdvance(badge) + 16 * unit
            br = QRectF(inner.right() - bw, inner.top(), bw, 20 * unit)
            bg = QColor(accent)
            bg.setAlpha(45)
            p.setPen(Qt.NoPen)
            p.setBrush(bg)
            p.drawRoundedRect(br, 10 * unit, 10 * unit)
            p.setPen(accent)
            p.drawText(br, Qt.AlignCenter, badge)

        value = T.render(tile.get("value", ""), self.states)
        number, _, unit_txt = value.rpartition(" ") if " " in value and not value.split(" ")[-1][:1].isdigit() else (value, "", "")
        if not number:
            number, unit_txt = value, ""
        big = min(52 * unit, rect.height() * 0.30)
        p.setPen(ALERT if alert else TEXT)
        p.setFont(self._font(big, light=True))
        fm = p.fontMetrics()
        vy = inner.top() + 24 * unit
        p.drawText(QRectF(inner.left(), vy, inner.width(), big * 1.25), Qt.AlignLeft | Qt.AlignVCenter, number)
        if unit_txt:
            nx = inner.left() + fm.horizontalAdvance(number) + 6 * unit
            p.setPen(DIM)
            p.setFont(self._font(big * 0.42))
            p.drawText(QRectF(nx, vy + big * 0.18, inner.width(), big * 1.0), Qt.AlignLeft | Qt.AlignVCenter, unit_txt)
        y = vy + big * 1.3

        if tile.get("gauge"):
            try:
                g = max(0.0, min(100.0, float(T.evaluate(tile["gauge"], self.states))))
            except (T.Missing, ValueError, TypeError):
                g = 0.0
            bar = QRectF(inner.left(), y, inner.width(), 8 * unit)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#2a2f37"))
            p.drawRoundedRect(bar, 4 * unit, 4 * unit)
            p.setBrush(ALERT if alert else accent)
            p.drawRoundedRect(QRectF(bar.left(), bar.top(), bar.width() * g / 100, bar.height()), 4 * unit, 4 * unit)
            y += 18 * unit

        sub = T.render(tile.get("sub", ""), self.states)
        if sub:
            p.setPen(DIM)
            p.setFont(self._font(min(14 * unit, rect.height() * 0.085)))
            p.drawText(QRectF(inner.left(), y, inner.width(), inner.bottom() - y), Qt.AlignLeft | Qt.AlignTop | Qt.TextWordWrap, sub)

    def _spark(self, p: QPainter, area: QRectF, s: Series, color: QColor, unit: float):
        vals = [v for _, v in s.points]
        lo, hi = min(vals + [0.0]), max(vals)
        if hi - lo < 1e-9:
            hi = lo + 1
        t0 = datetime.now(timezone.utc) - timedelta(hours=s.hours)
        span = s.hours * 3600

        def pt(t, v):
            x = area.left() + area.width() * max(0.0, (t - t0).total_seconds()) / span
            yy = area.bottom() - 6 * unit - (area.height() - 12 * unit) * (v - lo) / (hi - lo)
            return QPointF(x, yy)

        path = QPainterPath(pt(*s.points[0]))
        for t, v in s.points[1:]:
            path.lineTo(pt(t, v))
        fill = QPainterPath(path)
        fill.lineTo(QPointF(pt(*s.points[-1]).x(), area.bottom()))
        fill.lineTo(QPointF(pt(*s.points[0]).x(), area.bottom()))
        fill.closeSubpath()
        grad = QLinearGradient(area.topLeft(), area.bottomLeft())
        c1, c2 = QColor(color), QColor(color)
        c1.setAlpha(55)
        c2.setAlpha(0)
        grad.setColorAt(0, c1)
        grad.setColorAt(1, c2)
        p.save()
        clip = QPainterPath()
        clip.addRoundedRect(area.adjusted(0, -20, 0, 0), 14 * unit, 14 * unit)
        p.setClipPath(clip)
        p.setPen(Qt.NoPen)
        p.setBrush(grad)
        p.drawPath(fill)
        p.setBrush(Qt.NoBrush)
        line = QColor(color)
        line.setAlpha(150)
        p.setPen(QPen(line, 2 * unit))
        p.drawPath(path)
        p.restore()
