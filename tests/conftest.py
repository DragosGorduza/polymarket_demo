from __future__ import annotations

import sys
from pathlib import Path


# Make workspace root importable for tests (tracking/, execution/, polymarket_* packages).
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
