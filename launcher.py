"""
The frozen app's entry point.

The app's own code (DivinumOfficium.py, dooffice.py, dopdf.py ...) is run from
the plain app/ folder beside the exe when there is one, so an update is a few
.py files copied there (build-exe.py --quick) rather than a rebuilt exe; the
copies frozen inside the exe are the fallback. Windows does not lock these
files, so an update can be copied while the app is open: it takes effect the
next time the app starts.

Kept small on purpose: this file itself is frozen, so changing it needs a full
build.
"""

import importlib.abc
import importlib.util
import os
import sys

APP_DIR = os.path.join(os.path.dirname(os.path.abspath(sys.executable)), "app")


class AppFolderFinder(importlib.abc.MetaPathFinder):
    """Serves a top-level module from app/<name>.py, ahead of the frozen copies."""

    def find_spec(self, name, path=None, target=None):
        if path is not None or "." in name:
            return None
        source = os.path.join(APP_DIR, name + ".py")
        if not os.path.isfile(source):
            return None
        return importlib.util.spec_from_file_location(name, source)


if getattr(sys, "frozen", False) and os.path.isdir(APP_DIR):
    sys.meta_path.insert(0, AppFolderFinder())

import DivinumOfficium  # noqa: E402  (after the finder, so app/ is used)

sys.exit(DivinumOfficium.main())
