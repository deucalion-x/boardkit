"""Refuse a release tag the built artifacts do not agree with.

The version is derived from the git tag by hatch-vcs, so the two can no longer
be edited apart — but they can still disagree about whether this commit *is*
the tag. A shallow checkout, absent tags, or a tag moved after the fact all
produce a development version like `0.1.1.dev9+g5250ad5`, which would ship
under `RELEASE_TAG` as though it were that release.

Checked against the wheel rather than against metadata in the tree: the
filename is what the artifact will be installed as, and nothing can edit it
after the build.
"""

from __future__ import annotations

import os
import pathlib
import sys

tag = os.environ["RELEASE_TAG"]
where = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "dist")
wheels = sorted(where.glob("*.whl"))
if len(wheels) != 1:
    raise SystemExit(f"expected exactly one wheel in {where}, found {len(wheels)}")
# Wheel names are `name-version-pytag-abi-platform.whl`, so the version is the
# second field and the only one that can carry a dot.
built = wheels[0].name.split("-")[1]
if tag != f"v{built}":
    raise SystemExit(
        f"tag {tag} does not match the built version {built}. A development "
        "version here means the checkout is not at the tag, or carries no "
        "tags at all — hatch-vcs needs `fetch-depth: 0`.")
print(f"{tag} matches the built version")
