# Installation overview

{{BRAND}} is self-hosted first: one VM runs the portal, its Postgres database,
and one data volume. No cloud services are required, and the portal never
phones home.

## Choose a path

| You want to | Start at |
|---|---|
| Try writing reports, with no server at all | [Try Trellum locally](/docs/latest/install/try-it/) |
| Run the portal for your team | [Docker Compose](/docs/latest/install/docker-compose/) |
| Use a managed Postgres you already operate | [Configuration](/docs/latest/install/configuration/) |

The framework needs no installation of the portal — you can build reports on
your laptop first and stand up the portal later, against the same repository.
The [framework overview](/docs/latest/framework/the-framework/) explains the
boundary between the two; [framework capabilities](/docs/latest/framework/capabilities/)
is the index to the standalone API guide.

## What a deployment contains

- **web** — the portal itself, behind your reverse proxy
- **worker** — builds reports, one per instance, on schedule or on git push
- **db** — Postgres 16, which also holds the build queue, so there is no
  separate queue or cache service to run and monitor
- **a data volume** — built report output, one directory tree per studio

## Requirements

- Docker Engine 26+ with the compose plugin
- 2+ CPUs and 4+ GB RAM to start; report builds are the hungry part, so size
  to your reports rather than your viewer count
- A DNS name and a reverse proxy (Caddy, Traefik, nginx) terminating TLS,
  configured to accept large request bodies if you'll let people upload data
  files through the portal — see
  [Sizing](/docs/latest/operations/sizing/#uploaded-data-source-files)

!!! note
    Viewer traffic does not touch your warehouse — reports are pre-built — so
    you size the machine for building reports, not for reading them.

## Next

Continue to [Docker Compose](/docs/latest/install/docker-compose/) for the
install itself.
