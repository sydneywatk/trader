"""Pytest path setup.

Tests import the shared engine modules with ``from shared.X import ...``
(absolute, from the repo root), so the project root must be importable.
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
