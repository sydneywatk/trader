"""Pytest path setup.

Tests import platform modules two ways, mirroring the repo's own conventions
(see README "Conventions"):

  * shared modules     -> ``from shared.X import ...``      (absolute, from root)
  * strategy-local     -> ``from config import ...`` etc.   (bare, from strategy dir)

So both the project root and ``strategies/sid_method`` must be importable.
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SID_METHOD = os.path.join(ROOT, "strategies", "sid_method")

for path in (ROOT, SID_METHOD):
    if path not in sys.path:
        sys.path.insert(0, path)
