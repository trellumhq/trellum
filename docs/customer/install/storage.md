# Storage

Trellum uses three storage responsibilities. Choose them with the deployment
topology; there is no silent backend switch.

| Responsibility | One server | Multiple servers |
|---|---|---|
| PostgreSQL | One reachable database | One shared reachable database |
| Persistent filesystem | Local persistent volume | Shared filesystem such as NFS, EFS, or an equivalent |
| Built report artifacts | Local output by default, or optional S3-compatible bucket | Local output only when the filesystem is shared, or optional S3-compatible bucket |

## PostgreSQL

PostgreSQL stores application state and the shared work queue. A managed
PostgreSQL service is fine when configured through the [Configuration reference](/docs/latest/install/configuration/).

## Persistent filesystem

The data directory holds studio checkouts and project files, uploaded data
sources, live run logs, audit archives, scheduled-delivery inputs, and local
output and caches. On one host, the Docker persistent volume is local. Across
hosts, mount the same filesystem at the same path for web, coordinator, and
runners.

S3-compatible object storage does not replace these shared project files.

## Report artifacts

Built artifacts use local output by default, or an optional S3-compatible bucket
such as Amazon S3, MinIO, Ceph, or Wasabi. Builds stage files locally before upload, and the bucket
must also be available on the report-read path when configured for publication.

See [Configuration](/docs/latest/install/configuration/#built-report-output-in-object-storage)
for object-storage keys, preflight, and access, and [Backups & restore](/docs/latest/operations/backups-and-restore/)
for recovery. Do not copy partial active data or switch backends without a
planned migration.

### Private live-query manifests

New remote builds keep browser assets under
`{org}/{studio}/{report}/builds/{build}/` and host SQL manifests under
`_private/{org}/{studio}/{report}/builds/{build}/_live_queries.json`. The portal's
storage credentials need read, write, list and delete access to both namespaces.
The bucket must remain private. A report-scoped browser grant covers only the
public build prefix. Private manifests upload before the current-build pointer
changes and follow the same build-pruning and report/studio retention lifecycle.

When upgrading, deploy the updated `edge/report-access-worker.js` with the
portal. Custom `edge-external` gateways must deny `_live_queries.json`, its gzip
sibling, case variants, encoded path aliases and trailing dots/spaces for both
GET and HEAD, including NTFS stream suffixes such as `::$DATA`, and must never
serve `_private/`. The supplied worker denies these
requests before looking up an object, even with a valid report grant.

Existing builds can retain manifests in their old public build prefix; the host
reader supports them for compatibility, while portal and updated edge routes
deny downloads. Plan an operator-controlled migration or rebuild and removal of
those legacy manifests, and purge any previously cached manifest responses from
your CDN. Updating the portal alone does not update an external gateway or erase
cached responses. This upgrade does not automatically delete production objects.
