# Connecting a repository

A studio syncs one git repository. That repository is the source of truth for
its reports: nothing is authored in the portal UI.

## Configure the repository

In **Studio settings → Repository**, set:

- **URL** — the HTTPS clone URL
- **Token** — a read-only access token, stored encrypted; it is scrubbed from
  every log line
- **Branch** — usually `main`
- **Subdirectory** — when reports live in part of a larger repository
- **Poll interval** — how often to check for changes when no webhook is set up

Use **Sync now** to fetch immediately — the button reads **Check for changes**
in a studio set to publish manually.

The same page carries the **Publishing** switch, the pending change set and
the publish history; see
[Publishing from git](/docs/latest/portal/publishing-from-git/).

## Push-triggered builds

Polling is the fallback. For immediate builds, add a webhook in your git host
pointing at the studio's webhook URL, using the shared secret shown next to
it. Deliveries are verified with HMAC; unsigned or mismatched requests are
rejected. A delivery to a studio that publishes manually fetches only: the
change set waits for someone to publish it.

## What happens on a sync

1. The repository is fetched (sparse and partial, so large repositories stay
   cheap to sync)
2. What publishing the branch would change is worked out by diff — commits,
   reports added, modified or removed, project-root files, declared data
   sources — and recorded on the studio
3. In **Automatically**, that change set is published straight away; in
   **Manually** it waits for a person
4. Publishing validates every `report.yaml` first, then copies the changed
   report directories into the studio and re-scans
5. Only the changed reports rebuild — a one-line SQL change does not rebuild
   the studio
6. Reports deleted from the repository are removed from the portal; their
   built output is kept
7. The data sources the commit declares are mirrored into the studio's
   [Data sources](/docs/latest/portal/connecting-your-data-sources/) page

## Access tokens

Use a token with read access to exactly the one repository. The portal never
needs write access: it does not commit, tag, or open pull requests.

## Migrating an existing project

Already have a project directory outside the portal? Import it in one shot
instead of recreating it by hand — **the studio must already exist** (create
it first in **Manage studios → + New studio**; `import_project` only fills
an existing studio's project root, it does not create one):

```bash
docker compose run --rm -v /path/to/your/project:/seed web \
  python manage.py import_project <org>/<studio> /seed
```

Use `docker compose run -v ...`, not `docker compose exec` — `exec` runs
inside the already-started `web` container, which has no way to see a path
on your host unless it was already mounted when the stack came up. `run`
starts a fresh, throwaway container from the same image with the extra
mount attached, then exits once the import finishes.

This copies `reports/`, `data-sources/`, `config.yaml`, `events.yaml` and
`metrics.yaml` from the source directory into the studio, imports any
`data-sources/config.yaml` entries as data sources (credentials still need
to be entered in the portal UI), and re-scans the registry. The studio then
syncs from its configured repository as usual afterward.

## Next

- [Publishing from git](/docs/latest/portal/publishing-from-git/) — automatic
  and manual publishing, and the publish history
- [Connecting your data sources](/docs/latest/portal/connecting-your-data-sources/)
  — what the repository declares, and where the credentials go
- [The framework](/docs/latest/framework/the-framework/) — how reports are written
- [Build failures](/docs/latest/troubleshooting/build-failures/)
