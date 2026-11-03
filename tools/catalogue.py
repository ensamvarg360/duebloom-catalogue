#!/usr/bin/env python3
"""Validate and build the Duebloom service catalogue.

One file per service in services/<id>.json. `build` merges them with
catalogue.config.json into docs/v3/catalogue.json (format 3, prices per
region), and also writes docs/catalogue.json in format 2 for app builds that
predate format 3: US prices only, no regional links. GitHub Pages serves both.

    python3 tools/catalogue.py new <id>     create services/<id>.json from a template
    python3 tools/catalogue.py check        validate every service file
    python3 tools/catalogue.py build        validate, then write docs/catalogue.json
    python3 tools/catalogue.py links        request every cancellation URL (best effort)

Standard library only, Python 3.9+.
"""
import datetime
import json
import re
import sys
import urllib.error
import urllib.request
from decimal import Decimal, InvalidOperation
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVICES = ROOT / "services"
CONFIG = ROOT / "catalogue.config.json"
OUTPUT = ROOT / "docs" / "v3" / "catalogue.json"
OUTPUT_V2 = ROOT / "docs" / "catalogue.json"

SLUG = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
REGION = re.compile(r"^[A-Z]{2}$")
DOMAIN = re.compile(r"^[a-z0-9-]+(\.[a-z0-9-]+)+$")
UNITS = {"day", "week", "month", "year"}
# Same list as the app's CatalogueTests; the add screen's category chips use these.
CATEGORIES = {"Streaming", "Music", "Work", "Learning", "Fitness", "Storage", "News", "Gaming", "Other"}
# Matches CatalogueEntry.staleTestDays in the app: re-check prices before the app hides them.
STALE_DAYS = 180

# Key order in the built file, so diffs stay readable.
ENTRY_KEYS = ["id", "name", "aliases", "domain", "category", "cancellationURL", "regional", "prices"]
PRICE_KEYS = ["region", "checkedOn", "sourceURL", "trialDays", "plans"]
REGIONAL_KEYS = ["domain", "cancellationURL"]

# Format 2 has one price list per entry. Older apps show it only in this region.
V2_REGION = "US"
V2_NOTE = ("Plans, prices and trial lengths are US list prices in USD, read from the provider's own page "
           "on the date in priceInfo (see docs/PRICING.md). An entry with no priceInfo has no verified price. "
           "Prices are shown only for the region and only while recent (CatalogueEntry.localized).")
V2_ENTRY_KEYS = ["id", "name", "aliases", "domain", "category", "cancellationURL",
                 "trialDays", "typicalPlans", "priceInfo"]


def load_config():
    return json.loads(CONFIG.read_text())


def load_services():
    return {p.stem: (p, json.loads(p.read_text())) for p in sorted(SERVICES.glob("*.json"))}


def check_url(value, where, errors):
    if not isinstance(value, str) or not re.match(r"^https://[a-z0-9-]+(\.[a-z0-9-]+)+(/\S*)?$", value):
        errors.append(f"{where}: must be an https:// URL")


def check_entry(stem, e, today, errors, warnings):
    where = f"services/{stem}.json"
    if e.get("id") != stem:
        errors.append(f"{where}: id must equal the file name ({stem})")
    if not SLUG.match(str(e.get("id", ""))):
        errors.append(f"{where}: id must be a lowercase slug")
    for key in ("name", "category"):
        if not isinstance(e.get(key), str) or not e[key].strip():
            errors.append(f"{where}: {key} is required")
    if e.get("category") and e["category"] not in CATEGORIES:
        errors.append(f"{where}: category must be one of {sorted(CATEGORIES)}")
    if not DOMAIN.match(str(e.get("domain", ""))):
        errors.append(f"{where}: domain must be a bare domain such as netflix.com")
    if not isinstance(e.get("aliases", []), list):
        errors.append(f"{where}: aliases must be a list")
    if "cancellationURL" in e:
        check_url(e["cancellationURL"], f"{where} cancellationURL", errors)
    unknown = set(e) - set(ENTRY_KEYS)
    if unknown:
        errors.append(f"{where}: unknown keys {sorted(unknown)}")

    regional = e.get("regional", {})
    if not isinstance(regional, dict):
        errors.append(f"{where}: regional must map a region such as GB to its links")
        regional = {}
    for region, links in regional.items():
        rw = f"{where} regional.{region}"
        if not REGION.match(region):
            errors.append(f"{rw}: region must be ISO 3166 such as GB")
        if not isinstance(links, dict) or not links or set(links) - set(REGIONAL_KEYS):
            errors.append(f"{rw}: needs domain and/or cancellationURL, nothing else")
            continue
        if "domain" in links and not DOMAIN.match(str(links["domain"])):
            errors.append(f"{rw}: domain must be a bare domain such as amazon.co.uk")
        if "cancellationURL" in links:
            check_url(links["cancellationURL"], f"{rw} cancellationURL", errors)

    prices = e.get("prices", [])
    if not isinstance(prices, list):
        errors.append(f"{where}: prices must be a list")
        prices = []
    regions = [p.get("region") for p in prices]
    if len(set(regions)) != len(regions):
        errors.append(f"{where}: one prices block per region")
    for i, block in enumerate(prices):
        check_prices(block, f"{where} prices[{i}]", today, errors, warnings)


def check_prices(block, where, today, errors, warnings):
    unknown = set(block) - set(PRICE_KEYS)
    if unknown:
        errors.append(f"{where}: unknown keys {sorted(unknown)}")
    if not REGION.match(str(block.get("region", ""))):
        errors.append(f"{where}: region must be ISO 3166 such as US")
    check_url(block.get("sourceURL"), f"{where} sourceURL", errors)
    try:
        checked = datetime.date.fromisoformat(block.get("checkedOn", ""))
        age = (today - checked).days
        if age < 0:
            errors.append(f"{where}: checkedOn is in the future")
        elif age > STALE_DAYS:
            warnings.append(f"{where}: prices checked {age} days ago, re-check them")
    except (TypeError, ValueError):
        errors.append(f"{where}: checkedOn must be YYYY-MM-DD")
    if "trialDays" in block and not (isinstance(block["trialDays"], int) and block["trialDays"] > 0):
        errors.append(f"{where}: trialDays must be a positive whole number")
    plans = block.get("plans")
    if not isinstance(plans, list) or not plans:
        # A trial without plans can't show on the plan screen (DECISIONS #51).
        errors.append(f"{where}: plans is required and can't be empty")
        plans = []
    currencies = {plan.get("currencyCode") for plan in plans}
    if len(currencies) > 1:
        errors.append(f"{where}: one currency per region")

    for i, plan in enumerate(plans):
        pw = f"{where} plans[{i}]"
        if not plan.get("label"):
            errors.append(f"{pw}: label is required")
        try:
            if not isinstance(plan.get("amount"), str) or Decimal(plan["amount"]) < 0:
                raise InvalidOperation
        except InvalidOperation:
            errors.append(f'{pw}: amount must be decimal text such as "15.49"')
        if not re.match(r"^[A-Z]{3}$", str(plan.get("currencyCode", ""))):
            errors.append(f"{pw}: currencyCode must be ISO 4217 such as USD")
        interval = plan.get("interval", {})
        if interval.get("unit") not in UNITS or not (isinstance(interval.get("count"), int) and interval["count"] >= 1):
            errors.append(f"{pw}: interval needs unit (day/week/month/year) and count >= 1")


def validate():
    today = datetime.date.today()
    errors, warnings = [], []
    config = load_config()
    services = load_services()
    for stem, (_, entry) in services.items():
        check_entry(stem, entry, today, errors, warnings)
    popular = config.get("popular", {})
    if not isinstance(popular, dict) or not popular.get("default"):
        errors.append('catalogue.config.json: popular must map "default" and regions such as GB to lists of ids')
        popular = {}
    for region, ids in popular.items():
        if region != "default" and not REGION.match(region):
            errors.append(f"catalogue.config.json: popular key '{region}' must be default or ISO 3166")
        for pid in ids:
            if pid not in services:
                errors.append(f"catalogue.config.json: popular.{region} id '{pid}' has no service file")
    for w in warnings:
        print(f"warning: {w}")
    for e in errors:
        print(f"error: {e}")
    if errors:
        sys.exit(f"{len(errors)} error(s)")
    print(f"ok: {len(services)} services")
    return config, services


def ordered(d, keys):
    return {k: d[k] for k in keys if k in d}


def v2_entry(e):
    """The entry as format 2: its US prices flattened, regional links dropped."""
    flat = {k: e[k] for k in ("id", "name", "aliases", "domain", "category", "cancellationURL") if k in e}
    us = next((p for p in e.get("prices", []) if p["region"] == V2_REGION), None)
    if us:
        if "trialDays" in us:
            flat["trialDays"] = us["trialDays"]
        flat["typicalPlans"] = us["plans"]
        flat["priceInfo"] = {k: us[k] for k in ("region", "checkedOn", "sourceURL")}
    return ordered(flat, V2_ENTRY_KEYS)


def write(path, out):
    """Writes `out`, keeping the previous revision if nothing else changed.

    The app takes a download only if its revision is higher than what it has,
    so bump it (UTC yyyymmddHHMM) when the content changes and keep it otherwise.
    """
    previous = json.loads(path.read_text()) if path.exists() else {}
    unchanged = {k: v for k, v in previous.items() if k != "revision"} == {k: v for k, v in out.items() if k != "revision"}
    if unchanged and previous.get("revision"):
        out["revision"] = previous["revision"]
    else:
        out["revision"] = int(datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d%H%M"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n")
    print(f"wrote {path.relative_to(ROOT)} (revision {out['revision']}{', unchanged' if unchanged else ''})")


def build():
    config, services = validate()
    entries = []
    for _, e in services.values():
        entry = ordered(e, ENTRY_KEYS)
        if "prices" in entry:
            entry["prices"] = [ordered(p, PRICE_KEYS) for p in entry["prices"]]
        entries.append(entry)
    popular = config["popular"]
    write(OUTPUT, {"version": 3, "revision": 0, "note": config["note"], "popular": popular, "entries": entries})
    write(OUTPUT_V2, {"version": 2, "revision": 0, "note": V2_NOTE,
                      "popular": popular.get(V2_REGION, popular["default"]),
                      "entries": [v2_entry(e) for e in entries]})


def new(service_id):
    if not SLUG.match(service_id):
        sys.exit("id must be a lowercase slug such as disney-plus")
    path = SERVICES / f"{service_id}.json"
    if path.exists():
        sys.exit(f"{path.relative_to(ROOT)} already exists")
    template = {
        "id": service_id,
        "name": "",
        "aliases": [],
        "domain": "",
        "category": "",
        "cancellationURL": "https://",
        "prices": [{"region": "US", "checkedOn": datetime.date.today().isoformat(), "sourceURL": "https://",
                    "plans": [{"label": "", "amount": "0.00", "currencyCode": "USD",
                               "interval": {"unit": "month", "count": 1}}]}],
    }
    path.write_text(json.dumps(template, indent=2) + "\n")
    print(f"created {path.relative_to(ROOT)}; remove prices if you have none")


def links():
    failed = 0
    for stem, (_, e) in load_services().items():
        urls = [e.get("cancellationURL")] + [links.get("cancellationURL") for links in e.get("regional", {}).values()]
        for url in filter(None, urls):
            failed += check_link(stem, url)
    if failed:
        sys.exit(f"{failed} link(s) to look at by hand")


def check_link(stem, url):
    """Prints one link's status. Returns 1 if it needs a look by hand."""
    req = urllib.request.Request(url, method="GET", headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            status = r.status
    except urllib.error.HTTPError as err:
        status = err.code
    except Exception as err:  # noqa: BLE001 - report and move on
        status = type(err).__name__
    # 401/403 usually means a login wall or bot block, not a dead link.
    flag = "ok " if status in (200, 401, 403) else "BAD"
    print(f"{flag} {status} {stem}: {url}")
    return int(flag == "BAD")


if __name__ == "__main__":
    args = sys.argv[1:]
    if args[:1] == ["new"] and len(args) == 2:
        new(args[1])
    elif args in (["check"], ["build"], ["links"]):
        {"check": validate, "build": build, "links": links}[args[0]]()
    else:
        sys.exit(__doc__)
