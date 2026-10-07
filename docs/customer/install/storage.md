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
