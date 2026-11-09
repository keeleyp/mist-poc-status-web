"""Mist API access: org discovery and the per-org figures."""
import requests

REQUEST_TIMEOUT = 60
PAGE_LIMIT = 1000


class ApiError(Exception):
    pass


class MistApi:
    def __init__(self, session, ini_name="the .ini"):
        self.session = session
        self.ini_name = ini_name

    # -- HTTP -------------------------------------------------------------
    def get(self, host, token, path, params=None):
        """GET https://{host}/api/v1{path}. Returns the Response, or raises ApiError."""
        url = f"https://{host}/api/v1{path}"
        try:
            resp = self.session.get(url, headers={"Authorization": f"Token {token}"},
                                    params=params, timeout=REQUEST_TIMEOUT)
        except requests.exceptions.SSLError as e:
            raise ApiError(f"TLS error ({e}) - if you're behind Zscaler or a similar proxy, "
                           f"set [network] ca_bundle in {self.ini_name}")
        except requests.exceptions.ProxyError as e:
            raise ApiError(f"proxy error ({e}) - check [network] http_proxy / https_proxy "
                           f"in {self.ini_name}")
        except requests.exceptions.RequestException as e:
            raise ApiError(f"could not connect ({e.__class__.__name__})")

        if resp.status_code == 401:
            raise ApiError("HTTP 401 - token not valid on this cloud")
        if resp.status_code == 403:
            raise ApiError(f"HTTP 403 - token has no access to {path}")
        if resp.status_code == 429:
            raise ApiError("HTTP 429 - API rate limit reached (5000 calls/hour), try again later")
        if not resp.ok:
            raise ApiError(f"HTTP {resp.status_code} on {path}: {resp.text[:200]}")
        return resp

    def get_all(self, host, token, path, params=None):
        """Page through a list endpoint (limit/page) and return every item."""
        items, page = [], 1
        while True:
            resp = self.get(host, token, path, dict(params or {}, limit=PAGE_LIMIT, page=page))
            batch = resp.json()
            if not isinstance(batch, list):
                raise ApiError(f"unexpected response from {path} (not a list)")
            items.extend(batch)
            total = resp.headers.get("X-Page-Total")
            if len(batch) < PAGE_LIMIT or (total is not None and len(items) >= int(total)):
                return items
            page += 1

    def count_distinct(self, host, token, path, distinct, start, end):
        """Call a /count endpoint. Returns (rows, number of distinct values)."""
        data = self.get(host, token, path, {"distinct": distinct, "start": start, "end": end,
                                            "limit": PAGE_LIMIT}).json()
        results = data.get("results") or []
        if len(results) >= PAGE_LIMIT and data.get("total", 0) > len(results):
            return results, data["total"]  # more values than one response holds
        # The API can list the same value twice (seen with wired client MACs).
        return results, len({r.get(distinct) for r in results})

    # -- Orgs -------------------------------------------------------------
    def self_info(self, host, token):
        return self.get(host, token, "/self").json()

    def discover_orgs(self, host, token, self_data, note=print):
        """Return [(org_id, org_name)] the key can see, from /self privileges.
        MSP-level privileges are expanded to every org in that MSP. Site-level
        privileges are skipped (org-wide figures need org access)."""
        orgs, site_only = {}, set()
        for priv in self_data.get("privileges", []):
            scope = priv.get("scope")
            if scope == "org" and priv.get("org_id"):
                orgs[priv["org_id"]] = priv.get("name") or priv.get("org_name") or ""
            elif scope == "msp" and priv.get("msp_id"):
                for org in self.get_all(host, token, f"/msps/{priv['msp_id']}/orgs"):
                    orgs.setdefault(org["id"], org.get("name", ""))
            elif scope == "site" and priv.get("org_id"):
                site_only.add(priv["org_id"])
        for org_id in site_only - set(orgs):
            note(f"{host} key ...{token[-4:]} only has site-level access to org {org_id} - skipped")
        for org_id, name in orgs.items():
            if not name:
                orgs[org_id] = self.get(host, token, f"/orgs/{org_id}").json().get("name", org_id)
        return sorted(orgs.items(), key=lambda o: o[1].lower())

    # -- Figures ----------------------------------------------------------
    def collect_org(self, host, token, org_id, org_name, start, end, run_time, excluded_admins):
        row = {"date": run_time, "cloud": host, "org_name": org_name, "org_id": org_id}
        row["sites"] = len(self.get_all(host, token, f"/orgs/{org_id}/sites"))

        for dev_type in ("ap", "switch", "gateway"):
            row[f"{dev_type}_total"] = row[f"{dev_type}_online"] = row[f"{dev_type}_offline"] = 0
        for dev in self.get_all(host, token, f"/orgs/{org_id}/stats/devices", {"type": "all"}):
            dev_type = dev.get("type")
            if dev_type not in ("ap", "switch", "gateway"):
                continue
            row[f"{dev_type}_total"] += 1
            state = "online" if dev.get("status") == "connected" else "offline"
            row[f"{dev_type}_{state}"] += 1

        _, row["wireless_clients"] = self.count_distinct(
            host, token, f"/orgs/{org_id}/clients/count", "mac", start, end)
        _, row["wired_clients"] = self.count_distinct(
            host, token, f"/orgs/{org_id}/wired_clients/count", "mac", start, end)
        admins, _ = self.count_distinct(
            host, token, f"/orgs/{org_id}/logs/count", "admin_name", start, end)
        admins = [a for a in admins if not is_excluded_admin(a.get("admin_name"), excluded_admins)]
        row["audit_entries"] = sum(a.get("count", 0) for a in admins)
        row["audit_admins"] = len({a.get("admin_name") for a in admins})

        used = row["wireless_clients"] or row["wired_clients"] or row["audit_entries"]
        row["usage"] = "Active" if used else "Idle"
        return row


def is_excluded_admin(admin_name, excluded):
    """admin_name looks like 'First Last email@x.com'. Emails match as a whole
    word in it, anything else must match the whole name."""
    name = (admin_name or "").strip().lower()
    words = set(name.split())
    return any((x in words) if "@" in x else (x == name) for x in excluded)
