"""Mist POC status - shared core used by the web app and run_report.py.

  config     - read the .ini (clouds/keys, settings, network, web)
  mist_api   - Mist API client: org discovery and per-org figures
  collector  - one full collection run across every key and org
  store      - the Excel workbook (history, trends, POC details and notes)
  model      - the figures, POC detail fields and trend definitions
"""
__version__ = "1.0.0"
