#!/usr/bin/env python3
"""Sync the vendored models.dev snapshot, deliberately.

Fetches fresh copies of api.json and models.json, reports what changed against
the vendored snapshot, replaces it, stamps SNAPSHOT_DATE, and rebuilds the
database. Run by a human on purpose, never by CI on a schedule: an unreviewed
catalogue update is exactly the supply-chain surface the vendoring exists to
close, so the diff this prints is meant to be read before the result is
committed.

    python3 registry/sync.py            fetch, diff, replace, rebuild
    python3 registry/sync.py --dry-run  fetch and diff only
"""
from __future__ import annotations

import datetime
import json
import os
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
VENDOR = os.path.join(HERE, "vendor")
SOURCES = {
    "modelsdev-api.json": "https://models.dev/api.json",
    "modelsdev-models.json": "https://models.dev/models.json",
}


def fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "byoaik-registry-sync"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read()


def keys(payload: bytes) -> set[str]:
    return set(json.loads(payload))


def offerings(payload: bytes) -> set[tuple[str, str]]:
    d = json.loads(payload)
    return {(pid, mid) for pid, p in d.items() for mid in (p.get("models") or {})}


def main() -> int:
    dry = "--dry-run" in sys.argv
    fresh = {name: fetch(url) for name, url in SOURCES.items()}

    old_api = open(os.path.join(VENDOR, "modelsdev-api.json"), "rb").read()
    old_models = open(os.path.join(VENDOR, "modelsdev-models.json"), "rb").read()

    p_old, p_new = keys(old_api), keys(fresh["modelsdev-api.json"])
    m_old, m_new = keys(old_models), keys(fresh["modelsdev-models.json"])
    o_old, o_new = offerings(old_api), offerings(fresh["modelsdev-api.json"])

    print(f"providers: {len(p_old)} -> {len(p_new)}"
          f"  (+{len(p_new - p_old)} / -{len(p_old - p_new)})")
    for pid in sorted(p_new - p_old):
        print(f"   + {pid}")
    for pid in sorted(p_old - p_new):
        print(f"   - {pid}   <- a descriptor extending models.dev:{pid} now dangles")
    print(f"canonical models: {len(m_old)} -> {len(m_new)}"
          f"  (+{len(m_new - m_old)} / -{len(m_old - m_new)})")
    print(f"provider-model pairs: {len(o_old)} -> {len(o_new)}"
          f"  (+{len(o_new - o_old)} / -{len(o_old - o_new)})")

    if dry:
        print("\ndry run, nothing written")
        return 0

    for name, payload in fresh.items():
        open(os.path.join(VENDOR, name), "wb").write(payload)
    stamp = datetime.date.today().isoformat()
    open(os.path.join(VENDOR, "SNAPSHOT_DATE"), "w").write(stamp + "\n")
    print(f"\nsnapshot replaced, stamped {stamp}; rebuilding")
    rc = os.system(f'"{sys.executable}" "{os.path.join(HERE, "build.py")}"')
    return 1 if rc else 0


if __name__ == "__main__":
    raise SystemExit(main())
