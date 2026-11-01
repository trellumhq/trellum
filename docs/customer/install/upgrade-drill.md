# Upgrade helper verification record

Verified locally on 2026-11-01 with Docker Engine 29.6.0, Compose v2 and
Postgres 16. This record distinguishes the checks that ran from the full drill
that could not finish. **A migrated portal worker building a report with the
new runner, followed by a complete product rollback, remains unverified.** Do
not treat the database-only test as that proof.

## Repeatable checks that passed

From the repository, using a Python environment with the test dependencies:

```bash
TRELLUM_TEST_DB_NAME=test_trellum_cleanup168 python scripts/test.py local \
  apps/core/tests/test_deployment_helpers.py --workers 1
TRELLUM_TEST_DB_NAME=test_trellum_cleanup168 python scripts/test.py local \
  apps/core/tests/test_deployment_helpers.py::test_real_postgres_restore_and_failed_swap \
  --heavy --workers 1
bash -n scripts/upgrade.sh scripts/rollback.sh
DJANGO_SETTINGS_MODULE=trellum_portal.settings.test \
  TRELLUM_TEST_DB_NAME=test_trellum_cleanup168 \
  python manage.py makemigrations --check --dry-run
```

The ordinary suite checks paired default/custom references, both pins and the
backup manifest, runner pull/version failures before downtime, backup and
migration failures, incorrect serving version/schema, running-image mismatch,
conflicting exported pins, concurrent-operation locks, corrupt backups, fatal
restore failures and refusal to retry an unconfirmed rollback. Docker and
Compose are replaced by deterministic command adapters in those ordinary tests.

The heavy test uses a **new uniquely named** `postgres:16-alpine` container,
512 MiB RAM, one CPU, no published ports, disposable credentials and database
`test_trellum_cleanup168`. It runs the actual helper's dump/restore methods;
only the Compose `exec db` transport is adapted to that container. It:

1. Creates one fake table/row and saves a custom-format dump, verified with
   `pg_restore --list`.
2. Simulates an upgrade by adding a second row and a new table with a foreign
   key to the original table.
3. Restores into a scratch database and switches database names. Asserts one
   original row and no new table in the restored database; asserts both rows
   and the new table in the retained database.
4. Attempts to restore an invalid archive. Asserts failure and unchanged
   current data.
5. Injects a SQL error after both rename statements. Asserts the transaction
   reverts both names, the current data is unchanged, and the restored scratch
   database remains available for investigation.
6. Removes only its own container and its anonymous database volume in `finally`.

The separate published-image preflight also passed:

```bash
python3 - <<'PY'
import sys
sys.path.insert(0, 'scripts')
from deployment import Deployment
print(Deployment().prepare(
    'ghcr.io/trellumhq/trellum:v0.9.1',
    'ghcr.io/trellumhq/trellum-runner:v0.9.1', 'v0.9.1'))
PY
```

Verified portal ID:
`sha256:dcd0aab7805e099703bbb8194f3ef3fd3a5b71f363b05164d88d6cbdf9ced0e7`.
Verified runner ID:
`sha256:8234c15f0d078136c5ebf270bc0493dba4993ae03a62d53807c21c1d3f460c82`.
These checks pull and import both images, with no network inside the smoke
containers and 512 MiB/one CPU each. They do not verify signatures, submit a
build, or prove migration compatibility.

## Attempted full drill and blocker

To isolate the worker's Docker socket and sandbox orphan sweep from existing
services, the attempted v0.5.0 → v0.9.1 drill used a disposable nested Docker
daemon. No existing installation, shared Docker network, firewall, or host
cgroup settings were changed. The setup was:

```bash
fixture="$PWD/.data-test/upgrade168"
mkdir -p "$fixture/socket" "$fixture/scripts" "$fixture/docker"
cp scripts/deployment.py scripts/upgrade.sh scripts/rollback.sh "$fixture/scripts/"
cp docker-compose.yml "$fixture/"
cp docker/backup.sh docker/restore_drill.sh "$fixture/docker/"
docker run -d --name cleanup168-dind --privileged --memory 4g --cpus 2 \
  --entrypoint dockerd \
  -v "$fixture/socket:/var/run" -v "$fixture:$fixture" \
  docker:29-dind --host unix:///var/run/docker.sock --storage-driver=vfs
docker exec cleanup168-dind chmod 666 /var/run/docker.sock
export DOCKER_HOST="unix://$fixture/socket/docker.sock"
```

The fixture's `.env` selected the public v0.5.0 portal/runner pair, fake
Postgres/session/encryption values, in-memory email backend, update checks off,
Docker sandboxing, a fixture-only sandbox network and data volume, and Docker
group ID 0 for the nested socket. Its limits overlay capped Postgres at 512 MiB,
web/worker/migrate at 768 MiB each and backup at 256 MiB, within the outer 4 GiB
limit. No port from the nested daemon was published to the host. Initial start:

```bash
docker compose --project-directory "$fixture" -p cleanup168 \
  -f "$fixture/docker-compose.yml" -f "$fixture/limits.yml" up -d --no-build
```

Image pulls and container creation succeeded, but the old Postgres service
could not start:

```text
failed to create shim task: OCI runtime create failed: runc create failed:
unable to start container process: unable to apply cgroup configuration:
cannot enter cgroupv2 "/sys/fs/cgroup/docker" with domain controllers --
it is in an invalid state
```

The fixture therefore never reached old-product migration, report seeding,
helper upgrade, worker sandbox execution or helper rollback. No claims about
those stages follow from this attempt. The nested daemon container and its
anonymous storage were removed using its explicit name on the outer daemon.
The separate fake-data Postgres container used for the manual restore probe
was also removed; the automated heavy test cleans up its own fixture.

## Evidence still required

Repeat the complete procedure on a disposable host whose cgroups permit the
isolated worker Docker runtime. Start a healthy old portal/runner pair with
fake tenant/report/upload data and valid backups/cleanup state. Run the upgrade
helper; record migrations, both immutable pins and serving schema/version.
Queue a real report through the worker, assert successful output, and inspect
the sandbox's actual image ID against the new runner ID (configuration alone
is insufficient). Add a post-upgrade database sentinel, roll back with the
printed dump/manifest, and verify both old images, old schema/data, retained
post-upgrade database and unchanged uploaded files. Exercise failures on this
same isolated topology as well as the deterministic tests.

External Postgres, offline/mirror-disconnected deployment, release-only
overlays, split/multi-host workers and arbitrary custom mounts were not
verified and are excluded from these helpers' supported path. The restore test
proves database safeguards, not a complete disaster-recovery backup of uploads,
keys or object storage.
