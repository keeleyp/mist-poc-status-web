# Mist POC Status - Web

The web version of [mist-poc-status-report](https://github.com/keeleyp/mist-poc-status-report). It's a browser view of whether Juniper Mist POC orgs are actually being used, across several clouds and API keys, with POC details and notes kept against each org.

The text version is unchanged and still maintained separately. Both versions use **the same Excel workbook layout**, so they can share one file.

## What it shows

For every org each API key can see:
- sites
- APs, switches and gateways: online and offline
- unique wireless and wired clients over the last 24 hours
- audit log entries over the last 24 hours
- an **Active / Idle** flag. Active means at least one client, or one audit log entry by someone other than us, in the last 24 hours.

| Page | What's on it |
|---|---|
| **Orgs** (`/`) | The latest run for every org: active and idle counts, a filter box, *Idle only* and *Missing POC details* filters, and a **Run report now** button that shows live progress. |
| **Org** (`/org/<cloud>/<org id>`) | That org's figures, a link that opens it in Mist, the **POC details** form (Salesforce record, Sales email, SE email), **Notes** (add as many as you like, all shown newest first) and its full run history. |
| **Trends** (`/trends`) | One figure at a time across every run, with orgs down the side and ▲ / ▼ marking changes since the previous run. |
| **Download Excel** | The workbook itself. |

## Setup

```bash
pip install -r requirements.txt
cp mist_poc_status_web.ini.example mist_poc_status_web.ini
```

Edit `mist_poc_status_web.ini`. The `[clouds]`, `[settings]` and `[network]` sections are the same as in the text version:

```ini
[clouds]
eu  = TOKEN
gc3 = TOKEN_POC_A, TOKEN_POC_B     # several keys on one cloud

[settings]
output_file = mist_poc_status.xlsx  # can point at the text version's workbook
exclude_orgs =
exclude_admins =

[web]
host = 127.0.0.1                    # 0.0.0.0 to allow other machines
port = 5020
username =                          # optional login, set both or neither
password =
```

## Run

```bash
python3 app.py
```

Then open <http://127.0.0.1:5020>.

`--config other.ini` uses a different ini file. If you set `host` to anything other than this machine, also set a username and password. The app warns you when you don't.

### Scheduled runs

`run_report.py` collects one run without the web app, for cron or Task Scheduler. It uses the same ini and workbook, so its runs appear in the web app straight away:

```bash
python3 run_report.py
```

## How it's organised

```
app.py              Flask web app (pages, background run, basic auth)
run_report.py       one run from the command line, for scheduling
mist_poc/
  config.py         reads the ini; raises ConfigError instead of exiting
  mist_api.py       Mist API client: org discovery and per-org figures
  collector.py      one full run across all keys; fails fast with CollectionError
  store.py          the Excel workbook: history, trends, POC details, notes
  model.py          the figures, POC detail fields and email check
templates/, static/ HTML and CSS
```

The web app doesn't do any Mist or Excel work itself; it calls the `mist_poc` package. Storage lives only in `store.py`, so moving from Excel to a database later would mean replacing that one module.

## Behaviour

- **One run at a time.** **Run report now** starts a background run, and the page shows its progress. Pressing it again during a run just shows the run already in progress.
- **Stops on errors.** Every key is checked first. If any key or org fails, the run lists the failures and saves nothing, so the workbook never holds a partial run.
- **Run history.** Every run is time-stamped (`YYYY-MM-DD HH:MM`). A re-run within the same minute replaces that run's figures.
- **Saving.** Details and notes are saved straight to the workbook under a lock, so a background run and a form save can't overwrite each other. If Excel has the file locked (on Windows), the page asks you to close it and try again.
- **Your own activity is ignored.** Audit entries by the API key owners, and by any `exclude_admins`, are not counted.
- **Zscaler and proxies.** Set `[network] ca_bundle` to your proxy's root CA in PEM format. `http_proxy` and `https_proxy` can also be set there.
