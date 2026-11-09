"""The Excel workbook that holds everything: run history, trend sheets, POC
details and notes. The layout is the same as the text version
(mist-poc-status-report), so either tool can read the other's workbook.

Every change re-reads the workbook, applies the change and rewrites all
sheets under a lock, so the web app's background runs and form posts can't
overwrite each other. Reads are cached until the file changes on disk.

Storage lives only in this module - swapping Excel for a database later
means replacing this file, not the web app.
"""
import os
import threading
from dataclasses import dataclass
from datetime import datetime

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .model import COLUMNS, DETAIL_FIELDS, HEADERS, KEYS, TRENDS, org_key, sort_key

DETAILS_SHEET = "POC Details"
NOTES_SHEET = "POC Notes"
HEADER_FONT = Font(bold=True, color="FFFFFF")
HEADER_FILL = PatternFill("solid", fgColor="1F4E78")
ACTIVE_FILL = PatternFill("solid", fgColor="C6EFCE")
IDLE_FILL = PatternFill("solid", fgColor="FFC7CE")


class StoreError(Exception):
    pass


@dataclass
class Snapshot:
    history: list    # every run's rows, oldest first
    details: dict    # (cloud, org_id) -> {"org_name", sf_record, sales_email, se_email}
    notes: dict      # (cloud, org_id) -> [(date time, text)], oldest first

    @property
    def runs(self):
        return sorted({r["date"] for r in self.history})

    @property
    def last_run(self):
        runs = self.runs
        return runs[-1] if runs else None

    def latest_rows(self):
        last = self.last_run
        return sorted((r for r in self.history if r["date"] == last), key=sort_key)

    def org_history(self, key):
        return [r for r in self.history if org_key(r) == key]


class Store:
    def __init__(self, path):
        self.path = path
        self.lock = threading.RLock()
        self._cache = None  # (file signature, Snapshot)

    # -- reading ----------------------------------------------------------
    def _signature(self):
        try:
            st = os.stat(self.path)
            return (st.st_mtime_ns, st.st_size)
        except FileNotFoundError:
            return None

    def _open(self):
        if not os.path.exists(self.path):
            wb = Workbook()
            wb.remove(wb.active)
            return wb
        try:
            return load_workbook(self.path)
        except Exception as e:
            raise StoreError(f"Could not open {self.path} ({e}).")

    def snapshot(self):
        with self.lock:
            sig = self._signature()
            if self._cache and self._cache[0] == sig:
                return self._cache[1]
            snap = _read_snapshot(self._open())
            self._cache = (sig, snap)
            return snap

    # -- writing ----------------------------------------------------------
    def _save(self, wb):
        try:
            wb.save(self.path)
        except PermissionError:
            raise StoreError(f"Could not save {os.path.basename(self.path)} - "
                             f"it's probably open in Excel. Close it and try again.")
        self._cache = None

    def save_run(self, rows):
        """Add one run's rows. A run with the same timestamp (a re-run in the
        same minute) replaces the earlier figures for those orgs."""
        if not rows:
            return
        with self.lock:
            wb = self._open()
            snap = _read_snapshot(wb)
            run_time = rows[0]["date"]
            replaced = {org_key(r) for r in rows}
            history = [r for r in snap.history
                       if not (r["date"] == run_time and org_key(r) in replaced)]
            history.extend(rows)
            for r in rows:  # keep stored org names current
                if org_key(r) in snap.details:
                    snap.details[org_key(r)]["org_name"] = r["org_name"]
            _write_all(wb, Snapshot(history, snap.details, snap.notes))
            self._save(wb)

    def update_details(self, cloud, org_id, org_name, values):
        with self.lock:
            wb = self._open()
            snap = _read_snapshot(wb)
            d = snap.details.setdefault((cloud, org_id), {})
            d["org_name"] = org_name
            for _, key, _ in DETAIL_FIELDS:
                d[key] = (values.get(key) or "").strip()
            _write_all(wb, snap)
            self._save(wb)

    def add_note(self, cloud, org_id, org_name, text):
        text = (text or "").strip()
        if not text:
            return None
        with self.lock:
            wb = self._open()
            snap = _read_snapshot(wb)
            snap.details.setdefault((cloud, org_id), {"org_name": org_name})["org_name"] = org_name
            entry = (datetime.now().strftime("%Y-%m-%d %H:%M"), text)
            snap.notes.setdefault((cloud, org_id), []).append(entry)
            _write_all(wb, snap)
            self._save(wb)
            return entry


# --------------------------------------------------------------------------
# Workbook <-> Snapshot
# --------------------------------------------------------------------------
def _sheet_dicts(wb, title):
    if title not in wb.sheetnames:
        return []
    ws = wb[title]
    header = [c.value for c in ws[1]]
    return [dict(zip(header, v)) for v in ws.iter_rows(min_row=2, values_only=True) if any(v)]


def _read_snapshot(wb):
    history = []
    for rec in _sheet_dicts(wb, "History"):
        if rec.get("Date Time") is None:
            rec["Date Time"] = rec.get("Date")  # older workbooks: date only
        history.append({k: rec.get(h) for h, k in COLUMNS})
    history.sort(key=lambda r: (str(r["date"]), r["cloud"], (r["org_name"] or "").lower()))

    details = {}
    for rec in _sheet_dicts(wb, DETAILS_SHEET):
        d = {"org_name": rec.get("Org Name") or ""}
        for label, key, _ in DETAIL_FIELDS:
            d[key] = str(rec.get(label) or "").strip()
        details[(rec.get("Cloud"), rec.get("Org ID"))] = d

    notes = {}
    for rec in _sheet_dicts(wb, NOTES_SHEET):
        notes.setdefault((rec.get("Cloud"), rec.get("Org ID")), []).append(
            (str(rec.get("Date Time") or ""), str(rec.get("Note") or "")))
    return Snapshot(history, details, notes)


def _style_header(ws, n_cols):
    for c in range(1, n_cols + 1):
        cell = ws.cell(1, c)
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.alignment = Alignment(horizontal="center", wrap_text=True)
    ws.freeze_panes = "A2"


def _replace_sheet(wb, title, index=None):
    if title in wb.sheetnames:
        del wb[title]
    return wb.create_sheet(title, index) if index is not None else wb.create_sheet(title)


def _write_rows(wb, title, rows, index, extra=()):
    ws = _replace_sheet(wb, title, index)
    headers = HEADERS + [h for h, _, _ in extra]
    keys = KEYS + [k for _, k, _ in extra]
    ws.append(headers)
    for r in rows:
        ws.append([r.get(k) for k in keys])
    _style_header(ws, len(headers))
    widths = {"Date Time": 17, "Cloud": 18, "Org Name": 32, "Org ID": 38, "Usage": 9}
    widths.update({h: w for h, _, w in extra})
    for i, h in enumerate(headers, 1):
        ws.column_dimensions[get_column_letter(i)].width = widths.get(h, 12)
    usage_col = KEYS.index("usage") + 1
    for r in range(2, ws.max_row + 1):
        cell = ws.cell(r, usage_col)
        cell.fill = ACTIVE_FILL if cell.value == "Active" else IDLE_FILL
        for c in range(len(HEADERS) + 1, len(headers) + 1):
            ws.cell(r, c).alignment = Alignment(wrap_text=True, vertical="top")
    ws.auto_filter.ref = ws.dimensions


def trend_table(history, key):
    """(run times, [(cloud, org name, [value per run])]) - shared with the web UI."""
    runs = sorted({r["date"] for r in history})
    names = {}
    for r in sorted(history, key=lambda r: str(r["date"])):
        names[org_key(r)] = r["org_name"]
    values = {(r["cloud"], r["org_id"], r["date"]): r.get(key) for r in history}
    orgs = sorted(names, key=lambda k: (k[0], names[k].lower()))
    return runs, [(k[0], k[1], names[k], [values.get((k[0], k[1], d)) for d in runs]) for k in orgs]


def notes_text(entries):
    return "\n".join(f"{when} - {text}" for when, text in entries)


def _write_all(wb, snap):
    latest = []
    for r in snap.latest_rows():
        d = snap.details.get(org_key(r), {})
        latest.append(dict(r, **{k: d.get(k) for _, k, _ in DETAIL_FIELDS},
                           notes=notes_text(snap.notes.get(org_key(r), []))))
    extra = [(label, key, 28) for label, key, _ in DETAIL_FIELDS] + [("Notes", "notes", 70)]
    _write_rows(wb, "Latest", latest, 0, extra)
    _write_rows(wb, "History", snap.history, 1)

    for title, key in TRENDS:
        ws = _replace_sheet(wb, title)
        runs, table = trend_table(snap.history, key)
        ws.append(["Cloud", "Org Name"] + runs)
        for cloud, _, name, values in table:
            ws.append([cloud, name] + values)
        _style_header(ws, len(runs) + 2)
        ws.freeze_panes = "C2"
        ws.column_dimensions["A"].width = 18
        ws.column_dimensions["B"].width = 32
        for i in range(3, len(runs) + 3):
            ws.column_dimensions[get_column_letter(i)].width = 17

    ws = _replace_sheet(wb, DETAILS_SHEET)
    ws.append(["Cloud", "Org Name", "Org ID"] + [label for label, _, _ in DETAIL_FIELDS])
    for (cloud, org_id), d in sorted(snap.details.items(),
                                     key=lambda i: (i[0][0], (i[1].get("org_name") or "").lower())):
        if any(d.get(k) for _, k, _ in DETAIL_FIELDS):
            ws.append([cloud, d.get("org_name"), org_id] + [d.get(k) for _, k, _ in DETAIL_FIELDS])
    _style_header(ws, 3 + len(DETAIL_FIELDS))
    for col, width in zip("ABCDEF", (18, 32, 38, 24, 32, 32)):
        ws.column_dimensions[col].width = width

    ws = _replace_sheet(wb, NOTES_SHEET)
    ws.append(["Cloud", "Org Name", "Org ID", "Date Time", "Note"])
    for (cloud, org_id), entries in sorted(snap.notes.items()):
        name = snap.details.get((cloud, org_id), {}).get("org_name", "")
        for when, text in entries:
            ws.append([cloud, name, org_id, when, text])
    _style_header(ws, 5)
    for col, width in zip("ABCDE", (18, 32, 38, 17, 90)):
        ws.column_dimensions[col].width = width
    for r in range(2, ws.max_row + 1):
        ws.cell(r, 5).alignment = Alignment(wrap_text=True, vertical="top")
