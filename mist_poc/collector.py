"""One full collection run across every API key and org.

Fails fast: every key is checked first, and if any key, org discovery or org
collection fails, CollectionError lists every failure and nothing is
returned - so the workbook never holds a partial run.
"""
import time
from dataclasses import dataclass, field
from datetime import datetime

from .mist_api import ApiError, MistApi
from .model import sort_key

WINDOW_SECONDS = 24 * 3600


class CollectionError(Exception):
    def __init__(self, stage, failures):
        super().__init__(f"{stage}: {len(failures)} failure(s)")
        self.stage = stage
        self.failures = failures  # [(where, reason)]


@dataclass
class RunResult:
    run_time: str
    rows: list
    excluded_admins: list
    notes: list = field(default_factory=list)


def run_collection(cfg, progress=print):
    """Collect figures for every org. progress(message) is called as it goes."""
    api = MistApi(cfg.session, cfg.name)
    now = int(time.time())
    start, end = now - WINDOW_SECONDS, now
    run_time = datetime.now().strftime("%Y-%m-%d %H:%M")
    notes = []

    def note(msg):
        notes.append(msg)
        progress(msg)

    progress(f"Checking {len(cfg.keys)} API key(s)")
    failures, checked = [], []
    for host, token in cfg.keys:
        try:
            checked.append((host, token, api.self_info(host, token)))
        except ApiError as e:
            failures.append((f"{host} key ...{token[-4:]}", str(e)))
    if failures:
        raise CollectionError("Some API keys failed", failures)

    # Our own accounts' audit entries (key owners + exclude_admins) don't count.
    excluded = set(cfg.exclude_admins)
    for _, _, self_data in checked:
        owner = (self_data.get("email") or self_data.get("name") or "").strip().lower()
        if owner:
            excluded.add(owner)
    progress(f"Audit entries by these admins are not counted: {', '.join(sorted(excluded))}")

    work, seen = [], set()
    for host, token, self_data in checked:
        label = f"{host} key ...{token[-4:]} ({self_data.get('email') or self_data.get('name') or '?'})"
        try:
            orgs = api.discover_orgs(host, token, self_data, note)
        except ApiError as e:
            failures.append((label, f"listing orgs: {e}"))
            continue
        progress(f"{label}: {len(orgs)} org(s)")
        for org_id, org_name in orgs:
            if org_id.lower() in cfg.exclude_orgs or org_name.lower() in cfg.exclude_orgs:
                note(f"Excluded: {org_name}")
                continue
            if (host, org_id) in seen:
                continue  # same org visible to two keys on one cloud
            seen.add((host, org_id))
            work.append((host, token, org_id, org_name))
    if failures:
        raise CollectionError("Org discovery failed", failures)
    if not work:
        raise CollectionError("No orgs found", [("all keys", "no orgs found for any API key")])

    rows = []
    for i, (host, token, org_id, org_name) in enumerate(work, 1):
        progress(f"[{i}/{len(work)}] {host}  {org_name}")
        try:
            rows.append(api.collect_org(host, token, org_id, org_name, start, end,
                                        run_time, excluded))
        except ApiError as e:
            failures.append((f"{host} / {org_name}", str(e)))
    if failures:
        raise CollectionError("Some orgs failed", failures)

    return RunResult(run_time=run_time, rows=sorted(rows, key=sort_key),
                     excluded_admins=sorted(excluded), notes=notes)
