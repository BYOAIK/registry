-- BYOAIK registry, relational source.
--
-- The point of this shape is the separation the whole specification rests on:
-- a LAB builds a model, a PROVIDER serves it, and those are different parties.
-- An open-weights model typically has one lab and dozens of providers, each
-- using its own routing identifier. Per-provider JSON files cannot express that
-- without scanning every file, which is why the source is relational and the
-- JSON is a generated view.
--
-- Text files remain the contribution surface. This database is built from them,
-- never edited by hand: a provider opening a pull request must be able to read
-- and review what they are changing.

PRAGMA foreign_keys = ON;

CREATE TABLE lab (
  id    TEXT PRIMARY KEY,          -- 'openai', 'alibaba', 'meta-llama'
  name  TEXT
);

-- Canonical models, identified as lab/model. Model-intrinsic facts only:
-- anything that varies by who is serving it belongs on `offering` instead.
CREATE TABLE model (
  id            TEXT PRIMARY KEY,  -- 'openai/gpt-oss-120b'
  lab_id        TEXT NOT NULL REFERENCES lab(id),
  name          TEXT,
  family        TEXT,
  open_weights  INTEGER,           -- 1 if the weights are published
  context       INTEGER,           -- the lab's stated figure, not any deployment's
  max_output    INTEGER,
  tool_call     INTEGER,
  reasoning     INTEGER,
  attachment    INTEGER,
  release_date  TEXT
);

-- Build metadata: which snapshot this database was derived from.
CREATE TABLE meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE provider (
  id                  TEXT PRIMARY KEY,
  source              TEXT NOT NULL DEFAULT 'descriptor',  -- 'descriptor' | 'models.dev'
  name                TEXT,
  extends             TEXT,        -- advisory models.dev pointer
  credential_kind     TEXT NOT NULL,
  credential_scheme   TEXT,
  credential_header   TEXT,
  browser_direct      INTEGER,     -- callable from a page, or CORS-blocked
  -- Full-fidelity fields so a descriptor can be regenerated from this database.
  -- JSON-encoded where the source is a list; NULL always means "absent in the
  -- descriptor", which is distinct from present-and-empty.
  credential_hints    TEXT,
  credential_env      TEXT,
  serving_regions     TEXT,
  probe_list_models   TEXT,
  probe_responses_api INTEGER,
  probe_browser_note  TEXT,
  probe_quirks        TEXT,
  verified_notes      TEXT,
  jurisdiction_source_url TEXT,
  own_source          TEXT,
  own_source_url      TEXT,
  operator_country    TEXT,
  jurisdiction_source TEXT,        -- never inferred; NULL means not established
  default_contract    TEXT,        -- which contract operator_country summarises
  ownership_assessed  TEXT,        -- when ownership was last checked
  ultimate_parent     TEXT,        -- beneficial ownership, as published
  parent_country      TEXT,
  parent_listings     TEXT,        -- JSON array; '[]' = privately held, NULL = not established
  verified_date       TEXT,
  verified_method     TEXT         -- 'probed' | 'documentation' | 'operator'
);

CREATE TABLE surface (
  provider_id TEXT NOT NULL REFERENCES provider(id),
  id          TEXT NOT NULL,
  label       TEXT,
  url         TEXT NOT NULL,       -- may contain {vars}
  region      TEXT,
  PRIMARY KEY (provider_id, id)
);

-- Variables a templated surface URL requires; ordered by rowid.
CREATE TABLE surface_var (
  provider_id TEXT NOT NULL,
  surface_id  TEXT NOT NULL,
  name        TEXT NOT NULL,
  label       TEXT,
  required    INTEGER,
  pattern     TEXT,
  FOREIGN KEY (provider_id, surface_id) REFERENCES surface(provider_id, id)
);

-- The many-to-many. One row per string a provider will actually accept, per
-- surface where that is known. Entitlement is per surface: the same key saw 26
-- models on one Nebius surface, 24 on another and 19 on a third, so a row that
-- names no surface only means "somewhere at this provider". surface_id NULL =
-- surface not established (catalogue-derived); a concrete id = measured there.
CREATE TABLE offering (
  provider_id  TEXT NOT NULL REFERENCES provider(id),
  model_id     TEXT NOT NULL REFERENCES model(id),
  routing_id   TEXT NOT NULL,      -- what THIS provider expects in `model`
  surface_id   TEXT,               -- NULL = unknown surface
  source       TEXT NOT NULL DEFAULT 'catalogue',  -- 'descriptor' | 'catalogue'
  is_alternate INTEGER NOT NULL DEFAULT 0,
  note         TEXT,
  PRIMARY KEY (provider_id, routing_id, surface_id)
);

-- Probe observations, append-only. Capability truth is per deployment and
-- perishable: an operator can restart a serving engine with different flags and
-- nothing changes on the wire. So results are dated observations, never facts.
CREATE TABLE probe (
  provider_id TEXT NOT NULL REFERENCES provider(id),
  surface_id  TEXT,                -- observations are per deployment, so per surface
  routing_id  TEXT NOT NULL,
  observed_at TEXT NOT NULL,
  capability  TEXT NOT NULL,       -- 'tool_choice.auto', 'streaming', ...
  value       TEXT NOT NULL,       -- 'supported' | 'unsupported' | 'void'
  detail      TEXT
);

-- Disclosure-regime exposure, one attributed assessment per regime. A status,
-- never a verdict: whether a regime reaches a corporate structure is usually
-- contested, so rows record published facts and an assessment of them.
CREATE TABLE provider_exposure (
  provider_id TEXT NOT NULL REFERENCES provider(id),
  regime      TEXT NOT NULL,       -- 'us-cloud-act'
  status      TEXT NOT NULL,       -- 'none-identified'|'possible'|'likely'|'applies'
  basis       TEXT NOT NULL,
  source      TEXT,
  contract    TEXT,                -- scopes the assessment; NULL = provider-wide
  -- Keyed by contract as well as regime: one provider can have different
  -- exposure under the same regime depending on which entity you contract with,
  -- which is the whole reason contracts exist. NULL contract = provider-wide.
  PRIMARY KEY (provider_id, regime, contract)
);

-- Contracting arrangements. Entity country and governing law answer different
-- questions: the entity chain determines who can be compelled, governing law
-- only decides disputes. Kept in separate columns so nothing merges them.
CREATE TABLE contract (
  provider_id      TEXT NOT NULL REFERENCES provider(id),
  id               TEXT NOT NULL,
  applies_regions  TEXT,             -- JSON array
  applies_tiers    TEXT,
  applies_surfaces TEXT,
  applies_condition TEXT,
  entity           TEXT NOT NULL,
  entity_country   TEXT NOT NULL,
  governing_law    TEXT,
  venue            TEXT,
  source           TEXT,
  source_url       TEXT,
  note             TEXT,
  PRIMARY KEY (provider_id, id)
);

-- Dated jurisdictional observations, append-only in spirit. Narrative context
-- that structured fields cannot hold: market entries, restructuring, published
-- sovereignty commitments. Each row says when something was learned.
CREATE TABLE provider_note (
  provider_id TEXT NOT NULL REFERENCES provider(id),
  date        TEXT NOT NULL,
  topic       TEXT NOT NULL,       -- ownership|market-entry|restructuring|serving-footprint|sovereignty-commitment|exposure
  regime      TEXT,                -- set when topic = exposure
  note        TEXT NOT NULL,
  source      TEXT
);

CREATE INDEX idx_offering_model    ON offering(model_id);
CREATE INDEX idx_offering_provider ON offering(provider_id);
CREATE INDEX idx_probe_lookup      ON probe(provider_id, routing_id, capability);

-- Who serves each model, and under what name. The question a per-provider file
-- cannot answer.
CREATE VIEW model_availability AS
SELECT m.id            AS model_id,
       m.lab_id        AS lab,
       m.open_weights,
       p.id            AS provider_id,
       p.operator_country,
       p.browser_direct,
       o.routing_id,
       o.is_alternate,
       o.surface_id,
       s.region        AS region
FROM model m
JOIN offering o ON o.model_id = m.id
JOIN provider p ON p.id = o.provider_id
LEFT JOIN surface s ON s.provider_id = o.provider_id AND s.id = o.surface_id;
