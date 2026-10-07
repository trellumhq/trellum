# Choose how to run Trellum

Choose the shape that matches the job you need to do. A deployment is the whole
installation; a host is one machine in it.

## Choose a path

| You want to | Start at |
|---|---|
| Try it locally | [Try Trellum locally](/docs/latest/install/try-it/) — framework examples and an optional disposable portal demo |
| Run the portal on one server | [Deploy on one server](/docs/latest/install/docker-compose/) — web, worker, database, and persistent files |
| Build more reports at once on one server | [Scale builds on one server](/docs/latest/install/scale-builds/) — more runners on the same host |
| Run builds across multiple servers | [Run across multiple servers](/docs/latest/install/multiple-servers/) — runner hosts with shared PostgreSQL and files |

You can also build in CI and serve portable reports from a static host without
the portal. A managed PostgreSQL database and optional object storage are
choices inside these deployment shapes, not separate install methods.

The framework needs no installation of the portal — you can build reports on
your laptop first and stand up the portal later, against the same repository.
The [framework overview](/docs/latest/framework/the-framework/) explains the
boundary between the two; [framework capabilities](/docs/latest/framework/capabilities/)
is the index to the standalone API guide.

## What the terms mean

- **web** — the portal service
- **combined worker** — scheduling and report builds in one process
- **coordinator** — scheduling and maintenance; keep one active coordinator
- **runner** — claims queued work and builds reports
- **sandbox** — a temporary isolated container for one build

Additional coordinators stand by until they can acquire the coordinator lock.

## What a deployment contains

- **web** — the portal itself, behind your reverse proxy
- **worker, coordinator, runner** — the default worker combines scheduling
  and report builds. A split deployment uses one coordinator for scheduling,
  cleanup, and scheduled delivery, plus one or more runners for builds.
- **db** — Postgres 16, which also holds the build queue, so there is no
  separate queue or cache service to run and monitor
- **persistent data storage** — studio checkouts and project files, uploaded
  data-source files, live run logs, audit archives, and local report output;
  the web, coordinator, and every runner need access to the same data directory

## Requirements

- Docker Engine 26+ for the default named-volume storage, or Docker Engine 25+
  with [host-folder storage](/docs/latest/install/docker-compose/#docker-25-with-host-folder-storage).
  Use a current patched engine and the Compose plugin (v2.24+).
- 2+ CPUs and 4+ GB RAM to start; report builds are the hungry part, so size
  to your reports rather than your viewer count
- A DNS name and a reverse proxy (Caddy, Traefik, nginx) terminating TLS,
  configured to accept large request bodies if you'll let people upload data
  files through the portal — see
  [Sizing](/docs/latest/operations/sizing/#uploaded-data-source-files)

!!! note
    Build workload usually drives CPU and RAM. Serving pre-built reports also
    needs capacity, and reports with opt-in live queries use web and data-source
    capacity too.

## Next

Continue to [Deploy on one server](/docs/latest/install/docker-compose/) for the
complete installation, or choose [Storage](/docs/latest/install/storage/) before
planning a multi-host layout.
