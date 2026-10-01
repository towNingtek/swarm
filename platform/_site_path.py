"""Put the repository's site/ package on sys.path (import for side effect).

The platform drives sites through site/dsh_sitectl.py and friends. Set
SWARM_SITE_DIR when the site code is installed somewhere else.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

SITE_DIR = Path(os.environ.get("SWARM_SITE_DIR") or Path(__file__).resolve().parents[1] / "site")
if str(SITE_DIR) not in sys.path:
    sys.path.insert(0, str(SITE_DIR))
