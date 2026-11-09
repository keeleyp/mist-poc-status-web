#!/usr/bin/env python3
"""Mist POC Status - web app.

    python3 app.py [--config mist_poc_status_web.ini]

Then open http://127.0.0.1:5020 (host/port from the [web] section).

Pages:
  /                   latest run for every org, with a "Run report now" button
  /org/<cloud>/<id>   one org: figures, POC details form, notes, run history
  /trends             one figure across every run, orgs down the side
  /download           the Excel workbook
"""
import argparse
import functools
import os
import secrets
import sys
import threading
from datetime import datetime

from flask import (Flask, Response, abort, flash, jsonify, redirect, render_template,
                   request, send_file, url_for)

from mist_poc import __version__
from mist_poc.collector import CollectionError, run_collection
from mist_poc.config import ConfigError, load_config
from mist_poc.model import DETAIL_FIELDS, LABELS, TRENDS, org_key, validate_details
from mist_poc.store import Store, StoreError, trend_table

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_INI = os.path.join(BASE_DIR, "mist_poc_status_web.ini")
LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")
SHORT_LABELS = {"sf_record": "SF", "sales_email": "Sales", "se_email": "SE"}


class RunState:
    """The background collection run - one at a time."""

    def __init__(self):
        self.lock = threading.Lock()
        self.running = False
        self.started = self.finished = None
        self.log = []
        self.error = None
        self.failures = []
        self.run_time = None

    def as_dict(self):
        with self.lock:
            return {"running": self.running, "started": self.started, "finished": self.finished,
                    "log": list(self.log), "error": self.error, "failures": self.failures,
                    "run_time": self.run_time}


def manage_url(cloud, org_id):
    """Mist portal link for an org: api.<region>.mist.com -> manage.<region>.mist.com."""
    host = cloud.replace("api.", "manage.", 1) if cloud.startswith("api.") else cloud
    return f"https://{host}/admin/?org_id={org_id}"


def create_app(cfg):
    app = Flask(__name__)
    app.secret_key = secrets.token_hex(32)
    store = Store(cfg.output_file)
    state = RunState()

    @app.context_processor
    def globals_():
        return {"version": __version__, "workbook": os.path.basename(cfg.output_file),
                "warnings": cfg.warnings, "labels": LABELS}

    # -- optional basic auth ---------------------------------------------
    if cfg.web_username:
        @app.before_request
        def require_login():
            auth = request.authorization
            if not (auth and secrets.compare_digest(auth.username or "", cfg.web_username)
                    and secrets.compare_digest(auth.password or "", cfg.web_password)):
                return Response("Login required", 401,
                                {"WWW-Authenticate": 'Basic realm="Mist POC Status"'})

    def snapshot_or_error():
        try:
            return store.snapshot()
        except StoreError as e:
            abort(Response(str(e), 500))

    # -- background run ---------------------------------------------------
    def do_run():
        def progress(msg):
            with state.lock:
                state.log.append(f"{datetime.now():%H:%M:%S}  {msg}")
        try:
            result = run_collection(cfg, progress)
            progress(f"Saving {len(result.rows)} org(s) to {os.path.basename(cfg.output_file)}")
            store.save_run(result.rows)
            with state.lock:
                state.run_time = result.run_time
            progress("Done")
        except CollectionError as e:
            with state.lock:
                state.error = f"{e.stage} - nothing was saved."
                state.failures = e.failures
        except StoreError as e:
            with state.lock:
                state.error = str(e)
        except Exception as e:  # keep the app up whatever happens
            with state.lock:
                state.error = f"Unexpected error: {e.__class__.__name__}: {e}"
        finally:
            with state.lock:
                state.running = False
                state.finished = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    @app.post("/run")
    def start_run():
        with state.lock:
            if not state.running:
                state.running = True
                state.started = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                state.finished = state.error = state.run_time = None
                state.log, state.failures = [], []
                threading.Thread(target=do_run, daemon=True).start()
        return jsonify(state.as_dict())

    @app.get("/run/status")
    def run_status():
        return jsonify(state.as_dict())

    # -- pages ------------------------------------------------------------
    @app.get("/")
    def index():
        snap = snapshot_or_error()
        rows = []
        for r in snap.latest_rows():
            d = snap.details.get(org_key(r), {})
            missing = [SHORT_LABELS[key] for _, key, _ in DETAIL_FIELDS if not d.get(key)]
            rows.append(dict(r, missing=missing, n_notes=len(snap.notes.get(org_key(r), [])),
                             sf_record=d.get("sf_record", "")))
        active = sum(r["usage"] == "Active" for r in rows)
        return render_template("index.html", rows=rows, last_run=snap.last_run,
                               n_runs=len(snap.runs), active=active, idle=len(rows) - active,
                               run=state.as_dict())

    @app.get("/org/<cloud>/<org_id>")
    def org(cloud, org_id):
        snap = snapshot_or_error()
        key = (cloud, org_id)
        history = snap.org_history(key)
        if not history and key not in snap.details:
            abort(404)
        latest = history[-1] if history else None
        details = dict(snap.details.get(key, {}))
        org_name = (latest or {}).get("org_name") or details.get("org_name") or org_id
        return render_template("org.html", cloud=cloud, org_id=org_id, org_name=org_name,
                               latest=latest, history=list(reversed(history)),
                               details=details, errors={}, fields=DETAIL_FIELDS,
                               notes=list(reversed(snap.notes.get(key, []))),
                               in_last_run=bool(latest and latest["date"] == snap.last_run),
                               manage_url=manage_url(cloud, org_id))

    def org_name_for(snap, key):
        history = snap.org_history(key)
        if history:
            return history[-1]["org_name"]
        if key in snap.details:
            return snap.details[key].get("org_name") or key[1]
        abort(404)

    @app.post("/org/<cloud>/<org_id>/details")
    def save_details(cloud, org_id):
        snap = snapshot_or_error()
        key = (cloud, org_id)
        name = org_name_for(snap, key)
        values = {k: request.form.get(k, "").strip() for _, k, _ in DETAIL_FIELDS}
        errors = validate_details(values)
        if errors:
            history = snap.org_history(key)
            latest = history[-1] if history else None
            return render_template("org.html", cloud=cloud, org_id=org_id, org_name=name,
                                   latest=latest, history=list(reversed(history)),
                                   details=values, errors=errors, fields=DETAIL_FIELDS,
                                   notes=list(reversed(snap.notes.get(key, []))),
                                   in_last_run=bool(latest and latest["date"] == snap.last_run),
                                   manage_url=manage_url(cloud, org_id)), 400
        try:
            store.update_details(cloud, org_id, name, values)
            flash("POC details saved.", "ok")
        except StoreError as e:
            flash(str(e), "error")
        return redirect(url_for("org", cloud=cloud, org_id=org_id))

    @app.post("/org/<cloud>/<org_id>/notes")
    def add_note(cloud, org_id):
        snap = snapshot_or_error()
        name = org_name_for(snap, (cloud, org_id))
        text = request.form.get("note", "").strip()
        if not text:
            flash("The note was empty - nothing added.", "error")
        else:
            try:
                store.add_note(cloud, org_id, name, text)
                flash("Note added.", "ok")
            except StoreError as e:
                flash(str(e), "error")
        return redirect(url_for("org", cloud=cloud, org_id=org_id) + "#notes")

    @app.get("/trends")
    def trends():
        snap = snapshot_or_error()
        metric = request.args.get("metric", "wireless_clients")
        if metric not in {k for _, k in TRENDS}:
            metric = "wireless_clients"
        runs, table = trend_table(snap.history, metric)
        return render_template("trends.html", runs=runs, table=table, metric=metric,
                               metrics=[(k, t.replace("Trend ", "")) for t, k in TRENDS])

    @app.get("/download")
    def download():
        if not os.path.exists(cfg.output_file):
            flash("There is no workbook yet - run a report first.", "error")
            return redirect(url_for("index"))
        with store.lock:
            return send_file(cfg.output_file, as_attachment=True,
                             download_name=os.path.basename(cfg.output_file))

    return app


def main():
    parser = argparse.ArgumentParser(description="Mist POC Status web app")
    parser.add_argument("--config", default=DEFAULT_INI, help="path to the .ini file")
    args = parser.parse_args()
    try:
        cfg = load_config(args.config)
    except ConfigError as e:
        sys.exit(str(e))
    for w in cfg.warnings:
        print(f"WARNING: {w}")
    if cfg.web_host not in LOCAL_HOSTS and not cfg.web_username:
        print(f"WARNING: listening on {cfg.web_host} with no [web] username/password - anyone "
              f"who can reach this machine can see the report and edit POC details.")
    print(f"Mist POC Status {__version__}: http://{cfg.web_host}:{cfg.web_port}  "
          f"(workbook: {cfg.output_file})")
    create_app(cfg).run(host=cfg.web_host, port=cfg.web_port, threaded=True)


if __name__ == "__main__":
    main()
