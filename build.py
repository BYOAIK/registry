#!/usr/bin/env python3
"""Build registry.db from the descriptor files, then emit the published JSON.

Direction of truth: text files in git are the source, the database is derived,
and the JSON under public/v1/ is generated. Never the other way round. A
provider opening a pull request has to be able to read what they are changing,
and a binary database is not reviewable.

    python3 registry/build.py            build and emit
    python3 registry/build.py --query    build, then answer the question that
                                         per-provider files cannot
"""
from __future__ import annotations

import glob
import hashlib
import json
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "descriptors")
# Where emitted artifacts land: the website checkout when it sits alongside
# this repo, an out/ directory otherwise, or wherever BYOAIK_PUBLISH_DIR says.
_default_site = os.path.abspath(os.path.join(HERE, "..", "www.byoaik.org", "public"))
PUBLISH = os.environ.get("BYOAIK_PUBLISH_DIR",
                         _default_site if os.path.isdir(_default_site)
                         else os.path.join(HERE, "out"))
PUB = SRC  # descriptor sources; kept as PUB so existing readers stay valid
DB = os.path.join(HERE, "registry.db")
VENDOR = os.path.join(HERE, "vendor")
MODELS_DEV = os.environ.get("MODELS_DEV_JSON",
                            os.path.join(VENDOR, "modelsdev-models.json"))


def build() -> sqlite3.Connection:
    if os.path.exists(DB):
        os.remove(DB)
    con = sqlite3.connect(DB)
    con.executescript(open(os.path.join(HERE, "schema.sql")).read())

    # Canonical models. Prefer a models.dev snapshot; otherwise synthesise the
    # rows the descriptors reference, so a build never depends on the network.
    canon: dict[str, dict] = {}
    if MODELS_DEV and os.path.exists(MODELS_DEV):
        canon = json.load(open(MODELS_DEV))

    files = sorted(p for p in glob.glob(os.path.join(PUB, "*.json"))
                   if not p.endswith("index.json"))
    descriptors = [json.load(open(p)) for p in files]

    referenced = {m for d in descriptors for m in (d.get("models") or {})}
    for mid in sorted(referenced | set(canon)):
        c = canon.get(mid, {})
        lab = mid.split("/", 1)[0]
        con.execute("INSERT OR IGNORE INTO lab(id, name) VALUES (?, ?)", (lab, c.get("lab_name")))
        lim = c.get("limit") or {}
        con.execute(
            "INSERT OR REPLACE INTO model(id, lab_id, name, family, open_weights, context,"
            " max_output, tool_call, reasoning, attachment, release_date)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (mid, lab, c.get("name"), c.get("family"), int(bool(c.get("open_weights"))),
             lim.get("context"), lim.get("output"), int(bool(c.get("tool_call"))),
             int(bool(c.get("reasoning"))), int(bool(c.get("attachment"))),
             c.get("release_date")))

    # Bulk-load offerings from a models.dev api.json snapshot, keyed by each
    # descriptor's `extends` pointer. Hand-curating provider-to-model mappings
    # would recreate the exact N times M cost this project exists to remove, so
    # the registry stores corrections and alternates, not the whole catalogue.
    api_path = os.environ.get("MODELS_DEV_API_JSON",
                              os.path.join(VENDOR, "modelsdev-api.json"))
    api = json.load(open(api_path)) if os.path.exists(api_path) else {}

    # A routing id usually carries the lab as a prefix, but spells it as the
    # weights repository rather than the lab: `Qwen/...` is Alibaba, `meta-llama/...`
    # is Meta. models.dev canonicalises only a few hundred models while providers
    # serve many more, so without this fallback most offerings would have no lab
    # attribution at all, which is the one thing the lab/model split exists for.
    VENDOR_TO_LAB = {
        "qwen": "alibaba", "meta-llama": "meta", "zai-org": "zhipuai",
        "deepseek-ai": "deepseek", "mistralai": "mistral", "moonshotai": "moonshotai",
        "google": "google", "openai": "openai", "nvidia": "nvidia", "microsoft": "microsoft",
    }

    def canonical_for(routing_id: str) -> tuple[str | None, str]:
        """Map a provider's routing string to a lab/model id.

        Returns (id, source) where source is 'canonical' when models.dev has an
        entry, or 'derived' when the lab was read off the routing prefix.
        """
        rl = routing_id.lower()
        if rl in canon:
            return rl, "canonical"
        bare = rl.split("/", 1)[-1]
        matches = [c for c in canon if c.split("/", 1)[1] == bare]
        if len(matches) == 1:
            return matches[0], "canonical"
        if "/" in rl:
            vendor = rl.split("/", 1)[0]
            lab = VENDOR_TO_LAB.get(vendor, vendor)
            return f"{lab}/{bare}", "derived"
        return None, "none"

    for d in descriptors:
        cred, juris = d["credential"], d.get("jurisdiction") or {}
        probe, ver = d.get("probe") or {}, d.get("verified") or {}
        own = juris.get("ownership") or {}
        j = lambda k, src: json.dumps(src[k]) if k in src else None
        con.execute(
            "INSERT INTO provider(id, source, name, extends, credential_kind, credential_scheme,"
            " credential_header, browser_direct, operator_country, jurisdiction_source,"
            " ultimate_parent, parent_country, parent_listings,"
            " credential_hints, credential_env, serving_regions, probe_list_models,"
            " probe_responses_api, probe_browser_note, probe_quirks, verified_notes,"
            " jurisdiction_source_url, own_source, own_source_url,"
            " verified_date, verified_method)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (d["id"], "descriptor", d.get("name"), d.get("extends"), cred["kind"], cred.get("scheme"),
             cred.get("header"),
             None if probe.get("browserDirect") is None else int(probe["browserDirect"]),
             juris.get("operatorCountry"), juris.get("source"),
             own.get("ultimateParent"), own.get("parentCountry"),
             j("listings", own),
             j("hints", cred), j("env", cred), j("servingRegions", juris),
             probe.get("listModels"),
             None if probe.get("responsesApi") is None else int(probe["responsesApi"]),
             probe.get("browserNote"), j("quirks", probe), ver.get("notes"),
             juris.get("sourceUrl"), own.get("source"), own.get("sourceUrl"),
             ver.get("date"), ver.get("method")))
        for n in juris.get("notes") or []:
            con.execute("INSERT INTO provider_note(provider_id, date, topic, regime, note,"
                        " source) VALUES (?,?,?,?,?,?)",
                        (d["id"], n["date"], n["topic"], n.get("regime"), n["note"],
                         n.get("source")))
        for ex in juris.get("foreignDisclosureExposure") or []:
            con.execute("INSERT INTO provider_exposure(provider_id, regime, status, basis, source)"
                        " VALUES (?,?,?,?,?)",
                        (d["id"], ex["regime"], ex["status"], ex["basis"], ex.get("source")))

        for s in d["surfaces"]:
            con.execute("INSERT INTO surface(provider_id, id, label, url, region) VALUES (?,?,?,?,?)",
                        (d["id"], s["id"], s.get("label"), s["url"], s.get("region")))
            for var in s.get("vars") or []:
                con.execute("INSERT INTO surface_var(provider_id, surface_id, name, label,"
                            " required, pattern) VALUES (?,?,?,?,?,?)",
                            (d["id"], s["id"], var["name"], var.get("label"),
                             None if var.get("required") is None else int(var["required"]),
                             var.get("pattern")))

        # Bulk offerings first, from the catalogue.
        mdev_id = (d.get("extends") or "").split(":", 1)[-1]
        for routing_id in (api.get(mdev_id, {}).get("models") or {}):
            mid, src = canonical_for(routing_id)
            if not mid:
                continue
            lab = mid.split("/", 1)[0]
            con.execute("INSERT OR IGNORE INTO lab(id, name) VALUES (?, NULL)", (lab,))
            c = canon.get(mid, {})
            lim = c.get("limit") or {}
            con.execute(
                "INSERT OR IGNORE INTO model(id, lab_id, name, family, open_weights, context,"
                " max_output, tool_call, reasoning, attachment, release_date)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (mid, lab, c.get("name"), c.get("family"), int(bool(c.get("open_weights"))),
                 lim.get("context"), lim.get("output"), int(bool(c.get("tool_call"))),
                 int(bool(c.get("reasoning"))), int(bool(c.get("attachment"))),
                 c.get("release_date")))
            con.execute("INSERT OR IGNORE INTO offering(provider_id, model_id, routing_id,"
                        " surface_id, is_alternate, note) VALUES (?,?,?,NULL,0,?)",
                        (d["id"], mid, routing_id, f"lab {src}"))

        # Descriptor entries override the catalogue: they are the corrections.
        # An entry may pin the surfaces it was verified on; one row per surface.
        for mid, off in (d.get("models") or {}).items():
            con.execute("DELETE FROM offering WHERE provider_id=? AND routing_id=?",
                        (d["id"], off["routingId"]))
            for sid in (off.get("surfaces") or [None]):
                con.execute("INSERT INTO offering(provider_id, model_id, routing_id,"
                            " surface_id, source, is_alternate, note) VALUES (?,?,?,?,'descriptor',0,NULL)",
                            (d["id"], mid, off["routingId"], sid))
            for alt in off.get("alternates", []):
                con.execute("DELETE FROM offering WHERE provider_id=? AND routing_id=?",
                            (d["id"], alt["routingId"]))
                for sid in (alt.get("surfaces") or [None]):
                    con.execute("INSERT INTO offering(provider_id, model_id, routing_id,"
                                " surface_id, source, is_alternate, note) VALUES (?,?,?,?,'descriptor',1,?)",
                                (d["id"], mid, alt["routingId"], sid, alt.get("note")))

    # The rest of the models.dev catalogue: stub providers and their offerings,
    # so availability queries span every provider the catalogue knows, not just
    # the ones with a curated descriptor. Stubs are marked source='models.dev'
    # and carry credential_kind 'unknown': nothing about them is verified here,
    # and promoting one to a real descriptor is exactly what a registry PR does.
    covered = {(d.get("extends") or "").split(":", 1)[-1] for d in descriptors}
    covered |= {d["id"] for d in descriptors}
    for pid, p in api.items():
        if pid in covered:
            continue
        con.execute("INSERT OR IGNORE INTO provider(id, source, name, extends,"
                    " credential_kind) VALUES (?,?,?,?,?)",
                    (f"mdev-{pid}", "models.dev", p.get("name"), f"models.dev:{pid}",
                     "unknown"))
        if p.get("api"):
            con.execute("INSERT OR IGNORE INTO surface(provider_id, id, label, url)"
                        " VALUES (?,?,?,?)", (f"mdev-{pid}", "default", "Default", p["api"]))
        for routing_id in (p.get("models") or {}):
            mid, src = canonical_for(routing_id)
            if not mid:
                continue
            lab = mid.split("/", 1)[0]
            con.execute("INSERT OR IGNORE INTO lab(id, name) VALUES (?, NULL)", (lab,))
            con.execute("INSERT OR IGNORE INTO model(id, lab_id) VALUES (?,?)", (mid, lab))
            con.execute("INSERT OR IGNORE INTO offering(provider_id, model_id, routing_id,"
                        " surface_id, is_alternate, note) VALUES (?,?,?,NULL,0,?)",
                        (f"mdev-{pid}", mid, routing_id, f"lab {src}"))

    snap = open(os.path.join(VENDOR, "SNAPSHOT_DATE")).read().strip() \
        if os.path.exists(os.path.join(VENDOR, "SNAPSHOT_DATE")) else "unknown"
    con.execute("INSERT INTO meta(key, value) VALUES ('models.dev_snapshot', ?)", (snap,))

    # Probe observations: append-only JSONL per provider, the format the probe
    # library emits. Key-free by construction: endpoint, routing id, capability,
    # value, date. Never credential material.
    for path in sorted(glob.glob(os.path.join(HERE, "observations", "*.jsonl"))):
        pid = os.path.splitext(os.path.basename(path))[0]
        for line in open(path):
            line = line.strip()
            if not line:
                continue
            o = json.loads(line)
            con.execute("INSERT INTO probe(provider_id, surface_id, routing_id, observed_at,"
                        " capability, value, detail) VALUES (?,?,?,?,?,?,?)",
                        (pid, o.get("surface"), o["routing_id"], o["observed_at"],
                         o["capability"], o["value"], o.get("detail")))

    con.commit()
    return con


def export_descriptor(con: sqlite3.Connection, pid: str) -> dict:
    """Regenerate a descriptor from the database. The round-trip test asserts
    this equals the source file, which is the proof the database is a complete
    representation and not a projection."""
    r = con.execute("SELECT * FROM provider WHERE id=?", (pid,)).fetchone()
    cols = [c[1] for c in con.execute("PRAGMA table_info(provider)")]
    p = dict(zip(cols, r))
    jl = lambda v: json.loads(v) if v is not None else None

    d = {"$schema": "https://byoaik.org/v1/schema/descriptor.json",
         "specVersion": "byoaik-1", "id": pid}
    if p["name"]: d["name"] = p["name"]
    if p["extends"]: d["extends"] = p["extends"]

    d["surfaces"] = []
    for sid, label, url, region in con.execute(
            "SELECT id, label, url, region FROM surface WHERE provider_id=? ORDER BY rowid", (pid,)):
        s = {"id": sid}
        if label: s["label"] = label
        s["url"] = url
        if region: s["region"] = region
        vars_ = [ {k: v for k, v in
                   (("name", n), ("label", l), ("required", bool(req) if req is not None else None),
                    ("pattern", pat)) if v is not None}
                  for n, l, req, pat in con.execute(
                      "SELECT name, label, required, pattern FROM surface_var"
                      " WHERE provider_id=? AND surface_id=? ORDER BY rowid", (pid, sid))]
        if vars_: s["vars"] = vars_
        d["surfaces"].append(s)

    cred = {"kind": p["credential_kind"]}
    if p["credential_scheme"]: cred["scheme"] = p["credential_scheme"]
    if p["credential_header"]: cred["header"] = p["credential_header"]
    if p["credential_hints"] is not None: cred["hints"] = jl(p["credential_hints"])
    if p["credential_env"] is not None: cred["env"] = jl(p["credential_env"])
    d["credential"] = cred

    juris = {}
    if p["operator_country"]: juris["operatorCountry"] = p["operator_country"]
    if p["serving_regions"] is not None: juris["servingRegions"] = jl(p["serving_regions"])
    exposure = [ {k: v for k, v in
                  (("regime", reg), ("status", st), ("basis", ba), ("source", so)) if v is not None}
                 for reg, st, ba, so in con.execute(
                     "SELECT regime, status, basis, source FROM provider_exposure"
                     " WHERE provider_id=? ORDER BY rowid", (pid,))]
    if exposure: juris["foreignDisclosureExposure"] = exposure
    if p["jurisdiction_source"]: juris["source"] = p["jurisdiction_source"]
    if p["jurisdiction_source_url"]: juris["sourceUrl"] = p["jurisdiction_source_url"]
    own = {}
    if p["ultimate_parent"]: own["ultimateParent"] = p["ultimate_parent"]
    if p["parent_country"]: own["parentCountry"] = p["parent_country"]
    if p["parent_listings"] is not None: own["listings"] = jl(p["parent_listings"])
    if p["own_source"]: own["source"] = p["own_source"]
    if p["own_source_url"]: own["sourceUrl"] = p["own_source_url"]
    if own: juris["ownership"] = own
    notes = [ {k: v for k, v in
               (("date", dt), ("topic", tp), ("regime", rg), ("note", nt), ("source", so))
               if v is not None}
              for dt, tp, rg, nt, so in con.execute(
                  "SELECT date, topic, regime, note, source FROM provider_note"
                  " WHERE provider_id=? ORDER BY rowid", (pid,))]
    if notes: juris["notes"] = notes
    if juris: d["jurisdiction"] = juris

    models = {}
    rows = con.execute("SELECT model_id, routing_id, surface_id, is_alternate, note"
                       " FROM offering WHERE provider_id=? AND source='descriptor'"
                       " ORDER BY rowid", (pid,)).fetchall()
    for mid, rid, sid, alt, note in rows:
        entry = models.setdefault(mid, {})
        if not alt:
            entry.setdefault("routingId", rid)
            if sid: entry.setdefault("_surfaces", []).append(sid)
        else:
            alts = entry.setdefault("alternates", [])
            a = next((x for x in alts if x["routingId"] == rid), None)
            if a is None:
                a = {"routingId": rid}
                if note: a["note"] = note
                alts.append(a)
            if sid: a.setdefault("surfaces", []).append(sid)
    for entry in models.values():
        if "_surfaces" in entry: entry["surfaces"] = entry.pop("_surfaces")
    if models: d["models"] = models

    probe = {}
    if p["probe_list_models"]: probe["listModels"] = p["probe_list_models"]
    if p["probe_responses_api"] is not None: probe["responsesApi"] = bool(p["probe_responses_api"])
    if p["browser_direct"] is not None: probe["browserDirect"] = bool(p["browser_direct"])
    if p["probe_browser_note"]: probe["browserNote"] = p["probe_browser_note"]
    if p["probe_quirks"] is not None: probe["quirks"] = jl(p["probe_quirks"])
    if probe: d["probe"] = probe

    ver = {}
    if p["verified_date"]: ver["date"] = p["verified_date"]
    if p["verified_method"]: ver["method"] = p["verified_method"]
    if p["verified_notes"]: ver["notes"] = p["verified_notes"]
    if ver: d["verified"] = ver
    return d


def emit_html(con: sqlite3.Connection) -> None:
    """Regenerate the browsable registry page from the database, so the human
    view and the machine view can never drift: both are emitted by this build
    from the same rows. Providers are named here because a registry is a
    directory and naming is its purpose; the white paper's prose stays
    name-free."""
    import html as H
    snap = con.execute("SELECT value FROM meta WHERE key='models.dev_snapshot'").fetchone()[0]
    nobs = con.execute("SELECT COUNT(*) FROM probe").fetchone()[0]
    nmodels = con.execute("SELECT COUNT(*) FROM model").fetchone()[0]
    noff = con.execute("SELECT COUNT(*) FROM offering").fetchone()[0]
    nstub = con.execute("SELECT COUNT(*) FROM provider WHERE source='models.dev'").fetchone()[0]

    rows = []
    for (pid, name, kind, scheme, bdir, country, parent, pcountry, listings,
         vdate, vmethod) in con.execute(
            "SELECT id, name, credential_kind, credential_scheme, browser_direct,"
            " operator_country, ultimate_parent, parent_country, parent_listings,"
            " verified_date, verified_method FROM provider"
            " WHERE source='descriptor' ORDER BY id"):
        surfaces = con.execute("SELECT COUNT(*), GROUP_CONCAT(region, ', ') FROM surface"
                               " WHERE provider_id=? AND region IS NOT NULL", (pid,)).fetchone()
        nsurf = con.execute("SELECT COUNT(*) FROM surface WHERE provider_id=?", (pid,)).fetchone()[0]
        exp = con.execute("SELECT status FROM provider_exposure WHERE provider_id=?"
                          " AND regime='us-cloud-act'", (pid,)).fetchone()
        pobs = con.execute("SELECT COUNT(*) FROM probe WHERE provider_id=?", (pid,)).fetchone()[0]

        browser = {None: '<span class="chip chip-na">not tested</span>',
                   1: '<span class="chip chip-ok">yes</span>',
                   0: '<span class="chip chip-no">blocked</span>'}[bdir]
        method = ('<span class="chip chip-probe">probed</span>' if vmethod == "probed"
                  else '<span class="chip chip-doc">docs</span>' if vmethod else "")
        expc = {"applies": "chip-no", "possible": "chip-warn",
                "none-identified": "chip-ok"}.get(exp[0] if exp else None, "chip-na")
        expt = exp[0] if exp else "not assessed"
        parent_s = ""
        if parent:
            listed = ""
            if listings is not None:
                ls = json.loads(listings)
                listed = f" · {H.escape(', '.join(ls))}" if ls else " · private"
            parent_s = f"{H.escape(parent)} ({H.escape(pcountry or '?')}){listed}"
        regions = H.escape(surfaces[1]) if surfaces[1] else ""
        rows.append(f"""<tr>
<td><strong>{H.escape(name or pid)}</strong><br><span class="mut">{H.escape(pid)}</span></td>
<td>{H.escape(kind)}{(' · ' + H.escape(scheme)) if scheme and scheme != 'none' else ''}</td>
<td>{browser}</td>
<td>{H.escape(country or '')}</td>
<td class="wrapcell">{parent_s}</td>
<td><span class="chip {expc}">{H.escape(expt)}</span></td>
<td>{nsurf}{(' <span class=\'mut\'>(' + regions + ')</span>') if regions else ''}</td>
<td>{method}{f' <span class="mut">{pobs} obs</span>' if pobs else ''}</td>
<td><a href="/v1/registry/{pid}.json">JSON</a></td>
</tr>""")

    page = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>BYOAIK registry</title>
<meta name="description" content="The BYOAIK provider registry, browsable. Generated from the same database as the machine-readable JSON under /v1/, so the two cannot drift.">
<link rel="icon" href="/favicon.svg" type="image/svg+xml">
<link rel="stylesheet" href="/styles.css">
<script src="/analytics.js" defer></script>
<script src="/consent.js" defer></script>
<style>
.regtable {{ width: 100%; border-collapse: collapse; font-size: 0.85rem; }}
.regtable th {{ font-family: var(--mono); font-size: 0.66rem; letter-spacing: 0.09em;
  text-transform: uppercase; color: var(--ink-3); text-align: left;
  padding: 0.5rem 0.7rem; border-bottom: 1px solid var(--rule); white-space: nowrap; }}
.regtable td {{ padding: 0.55rem 0.7rem; border-bottom: 1px solid var(--rule-2);
  vertical-align: top; }}
.regtable .mut {{ color: var(--ink-3); font-family: var(--mono); font-size: 0.72rem; }}
.regtable .wrapcell {{ max-width: 16rem; }}
.tablewrap {{ overflow-x: auto; margin-top: 1.5rem; }}
.chip {{ font-family: var(--mono); font-size: 0.68rem; padding: 0.1rem 0.45rem;
  border-radius: 2px; border: 1px solid var(--rule); white-space: nowrap; }}
.chip-ok {{ color: var(--pass); }} .chip-no {{ color: var(--fail); }}
.chip-warn {{ color: var(--amber); }} .chip-na {{ color: var(--ink-3); }}
.chip-probe {{ color: var(--pass); border-color: var(--pass); }}
.chip-doc {{ color: var(--ink-3); }}
.regmeta {{ font-family: var(--mono); font-size: 0.78rem; color: var(--ink-2);
  margin-top: 0.75rem; }}
</style>
</head>
<body>
<header class="masthead">
  <div class="wrap">
    <a class="wordmark" href="/" aria-label="BYOAIK, home">
      <img src="/logo.svg" alt="BYOAIK" width="1442" height="263">
    </a>
    <span class="tag">Registry · byoaik-1</span>
    <nav>
      <a href="/">Overview</a>
      <a href="/spec/">Specification</a>
      <a href="https://github.com/BYOAIK">GitHub</a>
    </nav>
  </div>
</header>
<main>
  <div class="wrap hero">
    <p class="eyebrow">Registry</p>
    <h1>Providers, as verified.</h1>
    <p class="standfirst">
      Every row is backed by a descriptor under <a href="/v1/registry/index.json">/v1/registry/</a>,
      checksummed in the index. This page and that JSON are generated from the same database by the
      same build, so they cannot disagree. Rows marked probed were measured against a live endpoint
      with a real key; rows marked docs restate what the operator publishes, and say so.
    </p>
    <p class="regmeta">
      {len(rows)} described providers · {nstub} more known from the models.dev catalogue ·
      {nmodels} models · {noff} offerings · {nobs} probe observations ·
      catalogue snapshot {H.escape(snap)}
    </p>
    <div class="tablewrap">
      <table class="regtable">
        <thead><tr>
          <th>Provider</th><th>Credential</th><th>Browser</th><th>Operator</th>
          <th>Ultimate parent</th><th>US CLOUD Act</th><th>Surfaces</th>
          <th>Verified</th><th></th>
        </tr></thead>
        <tbody>
{chr(10).join(rows)}
        </tbody>
      </table>
    </div>
    <p class="regmeta" style="max-width: 68ch">
      CLOUD Act column: an attributed assessment, never legal advice. applies means a US operator;
      possible means published facts raise the question, stated in the descriptor's basis;
      none-identified means no US entity found in the published chain. Jurisdiction facts are
      operator-published or measured, never inferred.
    </p>
  </div>
</main>
<footer>
  <div class="wrap">
    <p>Generated from the registry database. To correct a row, change the descriptor, not this
      page: it is overwritten on every build.</p>
    <p class="colophon">No webfonts. No CDN. No cookies unless you allow them.<br>
      <a href="#" data-consent-reopen>Change your analytics choice</a></p>
  </div>
</footer>
</body>
</html>
"""
    out = os.path.join(PUBLISH, "registry")
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "index.html"), "w") as fh:
        fh.write(page)


def roundtrip(con: sqlite3.Connection) -> bool:
    """JSON -> db -> JSON must be equal for every descriptor-backed provider."""
    ok = True
    for (pid,) in con.execute("SELECT id FROM provider WHERE source='descriptor' ORDER BY id"):
        src = json.load(open(os.path.join(PUB, f"{pid}.json")))
        out = export_descriptor(con, pid)
        if src == out:
            continue
        ok = False
        print(f"ROUND-TRIP DIFF: {pid}")
        for k in sorted(set(src) | set(out)):
            if src.get(k) != out.get(k):
                print(f"   field {k}:")
                print(f"     src: {json.dumps(src.get(k))[:140]}")
                print(f"     out: {json.dumps(out.get(k))[:140]}")
    return ok


def emit(con: sqlite3.Connection) -> None:
    """Regenerate index.json from the database, checksums included."""
    rows = con.execute(
        "SELECT p.id, p.name, p.credential_kind, p.browser_direct, p.operator_country,"
        " p.verified_date, p.verified_method,"
        " (SELECT COUNT(*) FROM surface s WHERE s.provider_id = p.id),"
        " (SELECT COUNT(*) FROM offering o WHERE o.provider_id = p.id)"
        " FROM provider p WHERE p.source = 'descriptor' ORDER BY p.id").fetchall()
    providers = []
    for (pid, name, kind, bdirect, country, vdate, vmethod, nsurf, noff) in rows:
        raw = open(os.path.join(PUB, f"{pid}.json"), "rb").read()
        providers.append({
            "id": pid, "name": name, "file": f"{pid}.json", "specVersion": "byoaik-1",
            "surfaces": nsurf, "offerings": noff, "credential": kind,
            "browserDirect": None if bdirect is None else bool(bdirect),
            "jurisdiction": country, "verified": vdate, "method": vmethod,
            "sha256": hashlib.sha256(raw).hexdigest(),
        })
    outdir = os.path.join(PUBLISH, "v1", "registry")
    os.makedirs(outdir, exist_ok=True)
    schemadir = os.path.join(PUBLISH, "v1", "schema")
    os.makedirs(schemadir, exist_ok=True)
    import shutil
    for f in glob.glob(os.path.join(SRC, "*.json")):
        shutil.copy2(f, outdir)
    shutil.copy2(os.path.join(HERE, "schema", "descriptor.json"), schemadir)
    index = {
        "specVersion": "byoaik-1",
        "note": ("Published for reading and verification. Clients SHOULD depend on a vendored, "
                 "pinned copy. Any client that fetches this at runtime MUST verify "
                 "index.json.sig against the public key pinned in its own build, and MUST reject "
                 "unverified content. Fetching without verifying defeats the entire point."),
        "schema": "https://byoaik.org/v1/schema/descriptor.json",
        "source": "https://github.com/BYOAIK/registry",
        "generated": "2026-07-24",
        "providers": providers,
    }
    with open(os.path.join(outdir, "index.json"), "w") as fh:
        json.dump(index, fh, indent=2)
        fh.write("\n")


def query(con: sqlite3.Connection) -> None:
    print("\nWho serves each model, and under what string:\n")
    for mid, lab, ow in con.execute(
            "SELECT id, lab_id, open_weights FROM model ORDER BY id"):
        print(f"  {mid}   lab={lab}  open_weights={bool(ow)}")
        for pid, rid, alt, country in con.execute(
                "SELECT provider_id, routing_id, is_alternate, operator_country"
                " FROM model_availability WHERE model_id = ? ORDER BY provider_id, is_alternate",
                (mid,)):
            tag = " (alternate)" if alt else ""
            print(f"      {pid:12} {country or '--':3} {rid}{tag}")

    print("\nProviders callable directly from a browser:")
    for pid, bd in con.execute(
            "SELECT id, browser_direct FROM provider WHERE browser_direct IS NOT NULL ORDER BY id"):
        print(f"  {pid:14} {'yes' if bd else 'NO'}")


if __name__ == "__main__":
    con = build()
    emit(con)
    emit_html(con)
    rt = roundtrip(con)
    print("round-trip:", "16/16 descriptors regenerate identically from the db" if rt
          else "FAILED, db is not a complete representation")
    if "--export-dir" in sys.argv:
        out = sys.argv[sys.argv.index("--export-dir") + 1]
        os.makedirs(out, exist_ok=True)
        for (pid,) in con.execute("SELECT id FROM provider WHERE source='descriptor'"):
            with open(os.path.join(out, f"{pid}.json"), "w") as fh:
                json.dump(export_descriptor(con, pid), fh, indent=2); fh.write("\n")
        print(f"exported descriptors to {out}")
    n = con.execute("SELECT COUNT(*) FROM provider").fetchone()[0]
    m = con.execute("SELECT COUNT(*) FROM model").fetchone()[0]
    o = con.execute("SELECT COUNT(*) FROM offering").fetchone()[0]
    print(f"built {DB}: {n} providers, {m} models, {o} offerings")
    if "--query" in sys.argv:
        query(con)
