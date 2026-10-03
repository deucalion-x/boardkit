"""`python -m boardkit`, so a checkout can be run without being installed.

The `board` entry point resolves to whatever is on PATH, which on a machine
that also has boardkit installed is the installed copy rather than the one
being worked on. The contract run needs to exercise *this* source, so it goes
through here.
"""
from __future__ import annotations

import sys

from .cli import main

sys.exit(main())
