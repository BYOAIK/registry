# BYOAIK registry

The canonical source of the BYOAIK provider registry: the descriptors published at
[byoaik.org/v1/registry/](https://byoaik.org/v1/registry/index.json) and browsable at
[byoaik.org/registry/](https://byoaik.org/registry/) are generated from this repository.

BYOAIK (`byoaik-1`) is an open specification for accepting any AI provider's API key:
[byoaik.org](https://byoaik.org).

```
descriptors/       one JSON file per provider, the contribution surface
observations/      probe observations, append-only JSONL, key-free
schema/            JSON Schema for descriptors (published at /v1/schema/)
vendor/            dated models.dev snapshot (MIT), for reproducible builds
schema.sql         relational model
build.py           descriptors -> SQLite -> published JSON + browsable page
sync.py            refresh the models.dev snapshot, deliberately
```

## Build

```sh
python3 build.py
```

No dependencies beyond Python 3.10 and, for schema validation in development,
`jsonschema`. The build derives `registry.db`, runs a round-trip test proving every
descriptor regenerates identically from the database, and emits the published JSON,
checksummed index and browsable HTML. Output lands in a sibling `www.byoaik.org/public`
checkout when present, `out/` otherwise, or wherever `BYOAIK_PUBLISH_DIR` points.

## Contributing a provider

Add or correct `descriptors/<provider>.json` against the
[schema](https://byoaik.org/v1/schema/descriptor.json), then run the build; the round-trip
test and schema validation are the review gate. Ground rules, which reviews enforce:

- **Reported, never inferred.** Jurisdiction and ownership facts carry a `source` naming
  where the operator published them. A descriptor that guesses is worse than one that says
  nothing, so absent means not established.
- **Exposure entries are assessments, not verdicts.** Regime from the enum, a `status`, and
  a `basis` stating the published facts it rests on. Never legal advice.
- **Probed beats documented.** `verified.method` says which one a descriptor is, and
  observations in `observations/` are dated rows: endpoint, routing id, capability, value.
  Never key material, never account data.
- **The catalogue is not duplicated.** Model-intrinsic facts come from
  [models.dev](https://models.dev) via `extends`; this registry carries only what a
  catalogue structurally cannot: surfaces and their regions, credential shape, jurisdiction,
  ownership, quirks, and measurements.

## Trust properties

The published registry is meant to be **vendored and pinned** by applications. Anything
fetching it at runtime must verify the signed index against a key pinned in its own build,
because a descriptor decides where users' API keys are sent, and an unverified fetch would
make this repository's hosting a live attack surface. The same reasoning is why `sync.py`
is run by a human who reads the diff, never by CI on a schedule.
