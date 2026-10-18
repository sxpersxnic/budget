"""PyInstaller entry point for the installed app."""

import multiprocessing

if __name__ == "__main__":
    # must run before the heavy imports: native mode starts the window in a child process
    multiprocessing.freeze_support()
    from budget_app.main import run

    run()
