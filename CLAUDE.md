# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

This project uses [uv](https://github.com/astral-sh/uv) for dependency management with Python 3.13.

```bash
# Install dependencies
uv sync

# Run the app
uv run python __init__.py

# Add a dependency
uv add <package>
```

There are no tests or linting configured yet.

## Architecture

The entire application lives in `__init__.py` as a single-file tkinter GUI app with three classes:

- **`CSVManager`** — thread-safe file I/O. All reads/writes go through a `threading.Lock`. The CSV file (`MicroNote_YYYYMMDD.csv`) is created in the watched folder on startup and has columns: `Timestamp`, `Event`, `Notes`.

- **`FileEventHandler`** (`watchdog.FileSystemEventHandler`) — runs on a background thread managed by watchdog's `Observer`. It filters out the CSV file itself and pushes `("new_file", path)` tuples onto a `queue.Queue`.

- **`App`** — owns the tkinter main loop and bridges the two classes above. It polls the queue every 100ms via `root.after()` to safely dispatch file events onto the GUI thread (`_poll_queue` → `_handle_new_file`). Direct cross-thread GUI calls are avoided by design.

**Data flow:** watchdog thread → `queue.Queue` → tkinter `after()` poll → `CSVManager` write + table refresh.

The in-memory row list (`self._rows`) is kept in sync with the CSV manually — every append or update writes to both the CSV (via `CSVManager`) and `self._rows`, then calls `_refresh_table()` to redraw the `ttk.Treeview`.

On startup, `App.__init__` runs in order: pick folder → initialize CSV → build GUI → load existing rows → start watchdog observer → begin queue polling.
