#!/usr/bin/env python3
"""Collect one run from the command line, without the web app - for cron or
Task Scheduler. It uses the same ini and workbook as the web app, so
scheduled runs appear in the web app's history and trends.

    python3 run_report.py [--config mist_poc_status_web.ini]
"""
import argparse
import os
import sys

from mist_poc.collector import CollectionError, run_collection
from mist_poc.config import ConfigError, load_config
from mist_poc.store import Store, StoreError

DEFAULT_INI = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mist_poc_status_web.ini")


def print_table(rows):
    cols = [("Cloud", lambda r: r["cloud"]),
            ("Org", lambda r: r["org_name"]),
            ("Usage", lambda r: r["usage"]),
            ("Sites", lambda r: r["sites"]),
            ("APs on/off", lambda r: f"{r['ap_online']}/{r['ap_offline']}"),
            ("Switches on/off", lambda r: f"{r['switch_online']}/{r['switch_offline']}"),
            ("GWs on/off", lambda r: f"{r['gateway_online']}/{r['gateway_offline']}"),
            ("WiFi clients", lambda r: r["wireless_clients"]),
            ("Wired clients", lambda r: r["wired_clients"]),
            ("Audit entries", lambda r: r["audit_entries"])]
    cells = [[str(f(r)) for _, f in cols] for r in rows]
    widths = [max([len(h)] + [len(c[i]) for c in cells]) for i, (h, _) in enumerate(cols)]

    def fmt(values):
        return "  ".join(v.ljust(w) if i < 3 else v.rjust(w)
                         for i, (v, w) in enumerate(zip(values, widths)))

    print("\n" + fmt([h for h, _ in cols]))
    print("  ".join("-" * w for w in widths))
    for c in cells:
        print(fmt(c))
    active = sum(r["usage"] == "Active" for r in rows)
    print(f"\n{len(rows)} org(s): {active} active, {len(rows) - active} idle")


def main():
    parser = argparse.ArgumentParser(description="Collect one Mist POC status run")
    parser.add_argument("--config", default=DEFAULT_INI, help="path to the .ini file")
    args = parser.parse_args()
    try:
        cfg = load_config(args.config)
    except ConfigError as e:
        sys.exit(str(e))
    for w in cfg.warnings:
        print(f"WARNING: {w}")

    try:
        result = run_collection(cfg, progress=lambda msg: print(f"  {msg}"))
    except CollectionError as e:
        print(f"\n{e.stage} - stopping, nothing written.\n")
        width = max(len(w) for w, _ in e.failures)
        for where, reason in e.failures:
            print(f"  {where.ljust(width)}  {reason}")
        sys.exit(1)

    print_table(result.rows)
    try:
        Store(cfg.output_file).save_run(result.rows)
    except StoreError as e:
        sys.exit(str(e))
    print(f"Saved run {result.run_time} to {cfg.output_file}")


if __name__ == "__main__":
    main()
