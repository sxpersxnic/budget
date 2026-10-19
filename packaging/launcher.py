"""PyInstaller entry point for the installed app."""

import multiprocessing
import os
import sys

if __name__ == "__main__":
    # must run before the heavy imports: native mode starts the window in a child process
    multiprocessing.freeze_support()
    if sys.platform.startswith("linux"):
        os.environ.setdefault("PYWEBVIEW_GUI", "qt")  # the bundled backend; GTK would need system bindings
    from budget_app.main import run

    run()
