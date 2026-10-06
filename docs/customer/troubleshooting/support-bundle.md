# Support bundle

When something is wrong that the checks in the portal do not explain, send a
support bundle rather than a description of the symptoms.

```bash
docker compose exec web python manage.py supportbundle
# -> support-bundle-<timestamp>.tar.gz
```

## What is in it

- Health check results and component versions
- Configuration **shape** — which settings are set, not their values
- The worker fleet and recent run outcomes, with memory figures
- Git sync state per studio

## What is deliberately not in it

No report output, no query results, no data source credentials, no repository
tokens, no secrets. Secret settings appear only as present/absent with a
length. The archive uses support-bundle schema 3, which removes the obsolete
`TRELLUM_QUOTA_BACKEND` and `TRELLUM_EXTRA_URLCONFS` settings from diagnostics.
Consumers should reject an unknown schema instead of guessing at fields.

Inspect it yourself before sending — it is plain JSON inside the archive:

```bash
tar xzOf support-bundle-*.tar.gz support-bundle/bundle.json | less
```

!!! note
    The bundle is collected even when the install is badly broken — an
    unreadable database, a wrong encryption key. Each section fails
    independently and records why it failed, because that is exactly the
    situation where you need it.

## Before you send it

Run the built-in preflight; it often names the problem outright:

```bash
docker compose exec web python manage.py doctor
```
