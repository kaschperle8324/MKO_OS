#!/usr/bin/env python3
"""ClamAV-Attrappe: meldet Dateien mit dem EICAR-Teststring als infiziert."""
import os
import sys
import time

time.sleep(0.3)
paths = [a for a in sys.argv[1:] if not a.startswith("-")]
hit = False
for root in paths:
    files = [root] if os.path.isfile(root) else [os.path.join(d, f) for d, _, fs in os.walk(root) for f in fs]
    for f in files:
        if b"EICAR-STANDARD-ANTIVIRUS-TEST-FILE" in open(f, "rb").read():
            print(f"{f}: Eicar-Test-Signature FOUND")
            hit = True
sys.exit(1 if hit else 0)
