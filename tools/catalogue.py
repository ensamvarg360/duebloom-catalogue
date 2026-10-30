#!/usr/bin/env python3
"""Validate and build the Duebloom service catalogue.

One file per service in services/<id>.json. `build` merges them with
catalogue.config.json into docs/catalogue.json, which GitHub Pages serves.

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
OUTPUT = ROOT / "docs" / "catalogue.json"

SLUG = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
DOMAIN = re.compile(r"^[a-z0-9-]+(\.[a-z0-9-]+)+$")
UNITS = {"day", "week", "month", "year"}
# Same list as the app's CatalogueTests; the add screen's category chips use these.
CATEGORIES = {"Streaming", "Music", "Work", "Learning", "Fitness", "Storage", "News", "Gaming", "Other"}
# Matches CatalogueEntry.staleTestDays in the app: re-check prices before the app hides them.
STALE_DAYS = 180

# Key order in the built file, so diffs stay readable.
ENTRY_KEYS = ["id", "name", "aliases", "domain", "category", "cancellationURL",
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
    if "trialDays" in e and not (isinstance(e["trialDays"], int) and e["trialDays"] > 0):
        errors.append(f"{where}: trialDays must be a positive whole number")
    unknown = set(e) - set(ENTRY_KEYS)
    if unknown:
        errors.append(f"{where}: unknown keys {sorted(unknown)}")

    for i, plan in enumerate(e.get("typicalPlans", [])):
        pw = f"{where} typicalPlans[{i}]"
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

    info = e.get("priceInfo")
    if (e.get("typicalPlans") or "trialDays" in e) and info is None:
        errors.append(f"{where}: priceInfo is required when typicalPlans or trialDays is set")
    if info is not None:
        if not re.match(r"^[A-Z]{2}$", str(info.get("region", ""))):
            errors.append(f"{where} priceInfo: region must be ISO 3166 such as US")
        check_url(info.get("sourceURL"), f"{where} priceInfo sourceURL", errors)
        try:
            checked = datetime.date.fromisoformat(info.get("checkedOn", ""))
            age = (today - checked).days
            if age < 0:
                errors.append(f"{where} priceInfo: checkedOn is in the future")
            elif age > STALE_DAYS:
                warnings.append(f"{where}: prices checked {age} days ago, re-check them")
        except (TypeError, ValueError):
            errors.append(f"{where} priceInfo: checkedOn must be YYYY-MM-DD")


def validate():
    today = datetime.date.today()
    errors, warnings = [], []
    config = load_config()
    services = load_services()
    for stem, (_, entry) in services.items():
        check_entry(stem, entry, today, errors, warnings)
    for pid in config.get("popular", []):
        if pid not in services:
            errors.append(f"catalogue.config.json: popular id '{pid}' has no service file")
    for w in warnings:
        print(f"warning: {w}")
    for e in errors:
        print(f"error: {e}")
    if errors:
        sys.exit(f"{len(errors)} error(s)")
    print(f"ok: {len(services)} services")
    return config, services


def build():
    config, services = validate()
    entries = [{k: e[k] for k in ENTRY_KEYS if k in e} for _, e in services.values()]
    out = {"version": config["version"], "revision": 0, "note": config["note"],
           "popular": config.get("popular", []), "entries": entries}
    # The app takes a download only if its revision is higher than what it has,
    # so bump it (UTC yyyymmddHHMM) when the content changes and keep it otherwise.
    previous = json.loads(OUTPUT.read_text()) if OUTPUT.exists() else {}
    unchanged = {k: v for k, v in previous.items() if k != "revision"} == {k: v for k, v in out.items() if k != "revision"}
    if unchanged and previous.get("revision"):
        out["revision"] = previous["revision"]
    else:
        out["revision"] = int(datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d%H%M"))
    OUTPUT.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n")
    print(f"wrote {OUTPUT.relative_to(ROOT)} (revision {out['revision']}{', unchanged' if unchanged else ''})")


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
        "typicalPlans": [{"label": "", "amount": "0.00", "currencyCode": "USD",
                          "interval": {"unit": "month", "count": 1}}],
        "priceInfo": {"region": "US", "checkedOn": datetime.date.today().isoformat(),
                      "sourceURL": "https://"},
    }
    path.write_text(json.dumps(template, indent=2) + "\n")
    print(f"created {path.relative_to(ROOT)}; remove typicalPlans and priceInfo if you have no prices")


def links():
    failed = 0
    for stem, (_, e) in load_services().items():
        url = e.get("cancellationURL")
        if not url:
            continue
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
        failed += flag == "BAD"
        print(f"{flag} {status} {stem}: {url}")
    if failed:
        sys.exit(f"{failed} link(s) to look at by hand")


if __name__ == "__main__":
    args = sys.argv[1:]
    if args[:1] == ["new"] and len(args) == 2:
        new(args[1])
    elif args in (["check"], ["build"], ["links"]):
        {"check": validate, "build": build, "links": links}[args[0]]()
    else:
        sys.exit(__doc__)
