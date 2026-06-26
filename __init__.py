import os
import csv
import threading
import queue
import datetime
import pathlib
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog

try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler
except ImportError:
    import sys
    root = tk.Tk()
    root.withdraw()
    messagebox.showerror(
        "Missing dependency",
        "The 'watchdog' package is required.\n\nRun:  pip install watchdog\n\nthen restart the app."
    )
    sys.exit(1)

CSV_FILENAME = f"MicroNote_{datetime.date.today().strftime('%Y%m%d')}.csv"


class CSVManager:
    def __init__(self, filepath: pathlib.Path):
        self._filepath = filepath
        self._lock = threading.Lock()

    def initialize(self):
        if not self._filepath.exists():
            with open(self._filepath, "w", newline="", encoding="utf-8") as f:
                csv.writer(f).writerow(["Timestamp", "Event", "Notes"])

    def read_all(self) -> list:
        with self._lock:
            if not self._filepath.exists():
                return []
            with open(self._filepath, "r", newline="", encoding="utf-8") as f:
                return list(csv.DictReader(f))

    def append_row(self, timestamp: str, event: str, notes: str = ""):
        with self._lock:
            with open(self._filepath, "a", newline="", encoding="utf-8") as f:
                csv.writer(f).writerow([timestamp, event, notes])

    def update_notes(self, row_index: int, notes: str):
        with self._lock:
            if not self._filepath.exists():
                return
            with open(self._filepath, "r", newline="", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
            if row_index < len(rows):
                rows[row_index]["Notes"] = notes
            with open(self._filepath, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=["Timestamp", "Event", "Notes"])
                writer.writeheader()
                writer.writerows(rows)


class FileEventHandler(FileSystemEventHandler):
    def __init__(self, event_queue: queue.Queue, csv_filename: str):
        super().__init__()
        self._queue = event_queue
        self._csv_filename = csv_filename

    def _enqueue(self, path: str):
        if os.path.basename(path) != self._csv_filename:
            self._queue.put(("new_file", path))

    def on_created(self, event):
        if not event.is_directory:
            self._enqueue(event.src_path)

    def on_moved(self, event):
        if not event.is_directory:
            self._enqueue(event.dest_path)


class App:
    def __init__(self):
        self._root = tk.Tk()
        self._root.title("MicroNote")
        self._root.minsize(700, 400)

        self._rows: list = []
        self._selected_index = None
        self._unsaved_note = False
        self._status_after_id = None
        self._csv_manager = None
        self._observer = None
        self._event_queue = queue.Queue()
        self._watch_folder = None

        if not self._pick_folder():
            self._root.destroy()
            return

        self._setup_csv()
        self._build_gui()
        self._load_existing_rows()
        self._start_watcher()
        self._schedule_queue_poll()
        self._root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _pick_folder(self) -> bool:
        folder = filedialog.askdirectory(title="Select folder to watch", initialdir="D:/user_data")
        if not folder:
            messagebox.showinfo("Cancelled", "No folder selected. Exiting.")
            return False
        self._watch_folder = pathlib.Path(folder)
        return True

    def _setup_csv(self):
        csv_path = self._watch_folder / CSV_FILENAME
        self._csv_manager = CSVManager(csv_path)
        self._csv_manager.initialize()

    def _build_gui(self):
        self._root.columnconfigure(0, weight=1)
        self._root.rowconfigure(1, weight=1)

        # Header
        header = ttk.Frame(self._root, padding=4)
        header.grid(row=0, column=0, sticky="ew")
        ttk.Label(header, text=f"Watching: {self._watch_folder}").pack(side="left")
        ttk.Button(header, text="Add Event", command=self._add_manual_event).pack(side="right")

        # Table frame
        table_frame = ttk.Frame(self._root)
        table_frame.grid(row=1, column=0, sticky="nsew", padx=4)
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)

        cols = ("timestamp", "event", "notes")
        self._tree = ttk.Treeview(table_frame, columns=cols, show="headings", selectmode="browse")
        self._tree.heading("timestamp", text="Timestamp")
        self._tree.heading("event", text="Event")
        self._tree.heading("notes", text="Notes")
        self._tree.column("timestamp", width=160, stretch=True)
        self._tree.column("event", width=200, stretch=True)
        self._tree.column("notes", width=300, stretch=True)
        self._tree.grid(row=0, column=0, sticky="nsew")
        self._tree.bind("<<TreeviewSelect>>", self._on_row_select)

        scrollbar = ttk.Scrollbar(table_frame, orient="vertical", command=self._tree.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self._tree.configure(yscrollcommand=scrollbar.set)

        # Note entry area
        note_frame = ttk.Frame(self._root, padding=6)
        note_frame.grid(row=2, column=0, sticky="ew")
        note_frame.columnconfigure(1, weight=1)

        self._selected_label = ttk.Label(note_frame, text="Selected: (none)")
        self._selected_label.grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 4))

        ttk.Label(note_frame, text="Note:").grid(row=1, column=0, sticky="w", padx=(0, 4))

        self._note_var = tk.StringVar()
        self._note_entry = ttk.Entry(note_frame, textvariable=self._note_var, width=60)
        self._note_entry.grid(row=1, column=1, sticky="ew", padx=(0, 6))
        self._note_entry.bind("<Key>", self._on_note_changed)
        self._note_entry.bind("<Return>", lambda e: self._save_note())

        self._save_btn = ttk.Button(note_frame, text="Save Note", command=self._save_note, state="disabled")
        self._save_btn.grid(row=1, column=2, sticky="e")

        # Status bar
        self._status_var = tk.StringVar()
        status_bar = ttk.Label(self._root, textvariable=self._status_var, relief="sunken", anchor="w", padding=2)
        status_bar.grid(row=3, column=0, sticky="ew")

    def _load_existing_rows(self):
        self._rows = self._csv_manager.read_all()
        self._refresh_table()

    def _start_watcher(self):
        handler = FileEventHandler(self._event_queue, CSV_FILENAME)
        self._observer = Observer()
        self._observer.schedule(handler, path=str(self._watch_folder), recursive=False)
        self._observer.start()

    def _schedule_queue_poll(self):
        self._root.after(100, self._poll_queue)

    def _poll_queue(self):
        try:
            while True:
                kind, path = self._event_queue.get_nowait()
                if kind == "new_file":
                    self._handle_new_file(path)
        except queue.Empty:
            pass
        self._root.after(100, self._poll_queue)

    def _handle_new_file(self, filepath: str):
        filename = os.path.basename(filepath)
        # Dedupe: skip if already in rows
        if any(r["Event"] == filename for r in self._rows):
            return
        timestamp = datetime.datetime.now().isoformat(timespec="seconds")
        self._csv_manager.append_row(timestamp, filename)
        self._rows.append({"Timestamp": timestamp, "Event": filename, "Notes": ""})
        self._refresh_table()
        self._set_status(f"New file detected: {filename}")

    def _add_manual_event(self):
        comment = simpledialog.askstring("Add Comment", "Comment:", parent=self._root)
        if not comment:
            return
        comment = comment.strip()
        if not comment:
            return
        timestamp = datetime.datetime.now().isoformat(timespec="seconds")
        self._csv_manager.append_row(timestamp, "comment", comment)
        self._rows.append({"Timestamp": timestamp, "Event": "comment", "Notes": comment})
        self._refresh_table()
        self._set_status("Comment added.")

    def _refresh_table(self):
        prev_index = self._selected_index
        self._tree.delete(*self._tree.get_children())
        for row in self._rows:
            self._tree.insert("", "end", values=(row["Timestamp"], row["Event"], row["Notes"]))
        # Restore selection
        children = self._tree.get_children()
        if prev_index is not None and prev_index < len(children):
            self._tree.selection_set(children[prev_index])
            self._tree.see(children[prev_index])

    def _on_row_select(self, event=None):
        selection = self._tree.selection()
        if not selection:
            return
        item = selection[0]
        new_index = self._tree.index(item)

        if self._unsaved_note and new_index != self._selected_index:
            if not messagebox.askokcancel("Unsaved note", "You have an unsaved note. Discard changes?"):
                # Re-select previous row
                children = self._tree.get_children()
                if self._selected_index is not None and self._selected_index < len(children):
                    self._tree.selection_set(children[self._selected_index])
                return

        self._selected_index = new_index
        self._unsaved_note = False
        row = self._rows[self._selected_index]
        self._selected_label.config(text=f"Selected: {row['Event']}")
        self._note_var.set(row["Notes"])
        self._save_btn.config(state="normal")

    def _on_note_changed(self, event=None):
        self._unsaved_note = True

    def _save_note(self):
        if self._selected_index is None:
            return
        notes = self._note_var.get().strip()
        self._csv_manager.update_notes(self._selected_index, notes)
        self._rows[self._selected_index]["Notes"] = notes
        self._unsaved_note = False
        self._refresh_table()
        self._set_status("Note saved.")

    def _set_status(self, message: str):
        self._status_var.set(message)
        if self._status_after_id:
            self._root.after_cancel(self._status_after_id)
        self._status_after_id = self._root.after(3000, lambda: self._status_var.set(""))

    def _on_close(self):
        if self._unsaved_note:
            answer = messagebox.askyesnocancel("Unsaved note", "You have an unsaved note. Save before closing?")
            if answer is None:
                return
            if answer:
                self._save_note()
        if self._observer:
            self._observer.stop()
            self._observer.join()
        self._root.destroy()

    def run(self):
        self._root.mainloop()


def main():
    app = App()
    app.run()


if __name__ == "__main__":
    main()
