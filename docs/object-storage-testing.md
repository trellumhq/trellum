# Testing object storage locally

Runs the portal against MinIO standing in for S3, with **three runners that do
not share a filesystem with the web process or with each other**. Development
only — MinIO here has well-known credentials and no TLS.

## Why the unshared volumes are the whole point

With everything on one `trellum-data` volume, a passing test proves nothing: the
shared filesystem would serve the reports whether or not object storage worked
at all. `docker-compose.minio.yml` gives each runner its own anonymous volume at
`/data`, so no runner can see another's disk and none can see the web process's.

That makes the bucket the only path from a build to a reader. A report that
renders in the browser is a report that genuinely round-tripped through object
storage — which is exactly the claim we need to be able to make.

It is also the closest thing to a multi-host deployment you can run on one
laptop.

## Bring it up

```bash
export DOCKER_GID=$(getent group docker | cut -d: -f3)

docker compose -f docker-compose.yml -f docker-compose.minio.yml \
  --profile split up -d --scale runner=3
docker compose -f docker-compose.yml -f docker-compose.minio.yml stop worker
```

`worker` is the combined coordinator+runner from the default topology; the
`split` profile replaces it with one `coordinator` and N `runner`s, so it is
redundant here.

Check the wiring before trusting anything built on it:

```bash
docker compose exec web python manage.py doctor
```

`report storage` should read `s3://trellum-reports`. If it does not, inspect
the effective storage settings and run the preflight before proceeding.

The MinIO console is on <http://127.0.0.1:9001> (`minioadmin` / `minioadmin`).
Watching `trellum-reports` fill up while a build runs is the fastest way to see
this working.

## What to actually test

1. **A build reaches the browser at all.** Create a studio from a git
   repository, run a report, open it. Output was written on one runner's
   private disk, published, and served by a web process that has never seen
   that disk.

2. **Which runner built it does not matter.** Run the same report several
   times. With three runners the work lands on different ones; every result
   must be readable. Confirm from the run history which worker took each build.

3. **The registry does not stall.** A studio dashboard lists every report with
   its status and entry point. That path resolves the whole studio from one
   listing — if it were a round trip per report, a studio with thirty reports
   would visibly crawl.

4. **A rebuild replaces what readers see.** Change a report, rebuild, reload.
   The cache is keyed on the build's `last_run`, so a new build must land in a
   new cache directory rather than serving the old one.

5. **A broken bucket fails loudly, on both sides.** Stop MinIO
   (`docker compose stop minio`) and run a report. It must finish as **error**
   naming the publish failure, not as success — output that never reached the
   bucket is output nobody can open.

   Reading fails too, deliberately, and that is worth seeing for yourself:
   opening a report that is already sitting complete in a web node's cache
   still errors. The bucket is the source of truth once you have chosen it, and
   a cache entry is only a copy of one build — valid while the store agrees that
   build is current, and not a second opinion once it cannot be asked. Serving
   it anyway would hand the reader whichever version that node last happened to
   download. Restart MinIO and everything resumes with no intervention.

6. **Uploads are the known gap.** A file-backed data source will fail here, by
   design. Uploaded files travel the other way — the web process writes them, a
   build reads them — and still live on local disk. Use git-backed reports.
   This is the current boundary, not a fault in the setup.

## Storage policy

Object storage is available to every installation. Local storage remains the
explicit default, and switching to S3-compatible storage is an operator action.
There is no silent fallback: a broken bucket must fail loudly so an operator
can repair it or intentionally migrate back to local storage.

## Tear down

```bash
docker compose -f docker-compose.yml -f docker-compose.minio.yml \
  --profile split down -v
```

`-v` also drops the MinIO volume and the runners' anonymous data volumes. Leave
it off to keep a bucket between runs.

## Related

- `docker-compose.sim.yml` — the load-simulation override, same override
  pattern, different question (see [load-simulation.md](load-simulation.md))
- `apps/core/storage.py` — the storage layer these settings drive
