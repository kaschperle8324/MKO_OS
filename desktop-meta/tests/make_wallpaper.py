"""Erzeugt die MKO-OS-Hintergrundbilder (dunkler Verlauf mit dezentem Schriftzug)."""
import sys
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QLinearGradient, QPainter, QRadialGradient

app = QGuiApplication(sys.argv[:1])
out = Path(sys.argv[1] if len(sys.argv) > 1 else "data/wallpaper/contents/images")
out.mkdir(parents=True, exist_ok=True)
for w, h in ((1920, 1080), (2560, 1440), (3840, 2160), (1600, 1200), (1024, 768)):
    img = QImage(w, h, QImage.Format_RGB32)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    g = QLinearGradient(0, 0, w, h)
    g.setColorAt(0, QColor("#0b1622"))
    g.setColorAt(1, QColor("#101a2b"))
    p.fillRect(img.rect(), g)
    for cx, cy, r, col, a in ((0.78, 0.22, 0.6, "#1d5fa8", 120), (0.12, 0.9, 0.5, "#3daee9", 70)):
        rg = QRadialGradient(QPointF(w * cx, h * cy), max(w, h) * r)
        c = QColor(col)
        c.setAlpha(a)
        rg.setColorAt(0, c)
        c2 = QColor(c)
        c2.setAlpha(0)
        rg.setColorAt(1, c2)
        p.fillRect(img.rect(), rg)
    f = QFont()
    f.setPixelSize(int(h * 0.035))
    f.setWeight(QFont.Light)
    f.setLetterSpacing(QFont.AbsoluteSpacing, h * 0.006)
    p.setFont(f)
    p.setPen(QColor(255, 255, 255, 70))
    p.drawText(QRectF(0, 0, w - h * 0.05, h - h * 0.05), Qt.AlignRight | Qt.AlignBottom, "MKO OS")
    p.end()
    img.save(str(out / f"{w}x{h}.png"))
    print(out / f"{w}x{h}.png")
