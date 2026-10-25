from __future__ import annotations

import sys
from pathlib import Path


SAAS_ROOT = Path(__file__).resolve().parents[1]
for package_dir in ("core", "protocol", "agent", "server", "tests"):
    path = str(SAAS_ROOT / package_dir)
    if path not in sys.path:
        sys.path.insert(0, path)
