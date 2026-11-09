"""What is collected and stored - shared by the collector, store and web UI."""
import re

# (header, key) for every column in the History / Latest sheets.
COLUMNS = [
    ("Date Time", "date"),
    ("Cloud", "cloud"),
    ("Org Name", "org_name"),
    ("Org ID", "org_id"),
    ("Usage", "usage"),
    ("Sites", "sites"),
    ("APs Total", "ap_total"),
    ("APs Online", "ap_online"),
    ("APs Offline", "ap_offline"),
    ("Switches Total", "switch_total"),
    ("Switches Online", "switch_online"),
    ("Switches Offline", "switch_offline"),
    ("Gateways Total", "gateway_total"),
    ("Gateways Online", "gateway_online"),
    ("Gateways Offline", "gateway_offline"),
    ("Wireless Clients 24h", "wireless_clients"),
    ("Wired Clients 24h", "wired_clients"),
    ("Audit Log Entries 24h", "audit_entries"),
    ("Admins Active 24h", "audit_admins"),
]
HEADERS = [h for h, _ in COLUMNS]
KEYS = [k for _, k in COLUMNS]
LABELS = dict((k, h) for h, k in COLUMNS)

# POC details kept per org: (label, key, is_email). Notes are separate - a
# list of time-stamped entries per org.
DETAIL_FIELDS = [
    ("Salesforce Record", "sf_record", False),
    ("Sales Email", "sales_email", True),
    ("SE Email", "se_email", True),
]
EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")

# (Trend sheet name, key) - one trend per figure.
TRENDS = [
    ("Trend Sites", "sites"),
    ("Trend APs Online", "ap_online"),
    ("Trend APs Offline", "ap_offline"),
    ("Trend Switches Online", "switch_online"),
    ("Trend Switches Offline", "switch_offline"),
    ("Trend Gateways Online", "gateway_online"),
    ("Trend Gateways Offline", "gateway_offline"),
    ("Trend Wireless Clients", "wireless_clients"),
    ("Trend Wired Clients", "wired_clients"),
    ("Trend Audit Entries", "audit_entries"),
]


def org_key(row):
    """Orgs are identified by (cloud, org_id) everywhere."""
    return (row["cloud"], row["org_id"])


def sort_key(row):
    return (row["cloud"], (row.get("org_name") or "").lower())


def validate_details(values):
    """Return {label: error} for any detail value that isn't valid."""
    errors = {}
    for label, key, is_email in DETAIL_FIELDS:
        value = (values.get(key) or "").strip()
        if value and is_email and not EMAIL_RE.fullmatch(value):
            errors[key] = f"{label} doesn't look like an email address."
    return errors
