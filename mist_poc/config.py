"""Read the .ini. Problems raise ConfigError (the web app shows them; the
command line prints them) instead of exiting."""
import configparser
import os
from dataclasses import dataclass, field
from urllib.parse import urlparse

import requests


class ConfigError(Exception):
    pass


@dataclass
class Config:
    path: str
    keys: list                      # [(api_host, token)]
    output_file: str
    exclude_orgs: set = field(default_factory=set)
    exclude_admins: set = field(default_factory=set)
    session: requests.Session = None
    warnings: list = field(default_factory=list)
    web_host: str = "127.0.0.1"
    web_port: int = 5020
    web_username: str = ""
    web_password: str = ""

    @property
    def name(self):
        return os.path.basename(self.path)


def cloud_to_host(cloud):
    """Normalise an .ini cloud value to an API host name like api.eu.mist.com."""
    value = cloud.lower().strip()
    if "://" in value:
        value = urlparse(value).netloc
    value = value.strip("/")
    if value.endswith("mist.com"):
        if value.startswith("manage."):
            value = "api." + value[len("manage."):]
        return value
    if value in ("us", "global", "global01"):
        return "api.mist.com"
    return f"api.{value}.mist.com"


def _csv_set(value):
    return {x.strip().lower() for x in (value or "").split(",") if x.strip()}


def _build_session(parser, ini_name, warnings):
    """Shared HTTP session, wired up for a TLS-inspecting proxy (e.g. Zscaler)
    when the optional [network] section is present. Without it, behaviour is
    unchanged (certifi CA store, HTTP_PROXY / HTTPS_PROXY env vars)."""
    session = requests.Session()
    if "network" not in parser:
        return session
    section = parser["network"]
    ca_bundle = section.get("ca_bundle", "").strip()
    if ca_bundle:
        ca_path = os.path.expanduser(ca_bundle)
        if not os.path.isfile(ca_path):
            raise ConfigError(f"[network] ca_bundle in {ini_name} does not exist: {ca_path}. "
                              f"It should be a PEM file with your proxy's root CA certificate "
                              f"(e.g. exported from Zscaler).")
        session.verify = ca_path
    elif not section.getboolean("verify_ssl", fallback=True):
        session.verify = False
        warnings.append("TLS certificate verification is DISABLED ([network] verify_ssl = false). "
                        "Only use this as a last resort.")
        requests.packages.urllib3.disable_warnings(requests.packages.urllib3.exceptions.InsecureRequestWarning)
    for scheme in ("http", "https"):
        proxy = section.get(f"{scheme}_proxy", "").strip()
        if proxy:
            session.proxies[scheme] = proxy
    return session


def load_config(path):
    ini_name = os.path.basename(path)
    parser = configparser.ConfigParser()
    parser.optionxform = str  # keep cloud names exactly as written
    if not parser.read(path):
        raise ConfigError(f"{ini_name} not found - copy {ini_name}.example and fill it in.")

    if "clouds" not in parser or not parser["clouds"]:
        raise ConfigError(f"No clouds listed - add 'cloud = api_token' lines under [clouds] in {ini_name}.")
    keys = []
    for cloud, value in parser["clouds"].items():
        tokens = [t.strip() for t in value.split(",") if t.strip()]
        if not tokens or any(t.startswith("YOUR_") for t in tokens):
            raise ConfigError(f"No API token set for '{cloud}' in {ini_name}.")
        host = cloud_to_host(cloud)
        for token in tokens:
            if (host, token) in keys:
                raise ConfigError(f"The same token is listed twice for {host} in {ini_name}.")
            keys.append((host, token))

    base = os.path.dirname(os.path.abspath(path))
    output = os.path.expanduser(parser.get("settings", "output_file", fallback="mist_poc_status.xlsx"))
    warnings = []
    cfg = Config(
        path=os.path.abspath(path),
        keys=keys,
        output_file=os.path.join(base, output),
        exclude_orgs=_csv_set(parser.get("settings", "exclude_orgs", fallback="")),
        exclude_admins=_csv_set(parser.get("settings", "exclude_admins", fallback="")),
        session=_build_session(parser, ini_name, warnings),
        warnings=warnings,
        web_host=parser.get("web", "host", fallback="127.0.0.1").strip() or "127.0.0.1",
        web_port=parser.getint("web", "port", fallback=5020),
        web_username=parser.get("web", "username", fallback="").strip(),
        web_password=parser.get("web", "password", fallback="").strip(),
    )
    if bool(cfg.web_username) != bool(cfg.web_password):
        raise ConfigError(f"[web] in {ini_name}: set both username and password, or neither.")
    return cfg
