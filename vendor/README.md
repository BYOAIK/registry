# Vendored models.dev snapshot

`modelsdev-api.json` and `modelsdev-models.json` are dated snapshots of
https://models.dev/api.json and https://models.dev/models.json (MIT licensed,
https://github.com/anomalyco/models.dev). Vendored so the registry build is
reproducible without network access, and so a build states which snapshot it
was derived from. Refresh deliberately, not automatically:

    curl -o modelsdev-api.json https://models.dev/api.json
    curl -o modelsdev-models.json https://models.dev/models.json

then update SNAPSHOT_DATE and rebuild.
