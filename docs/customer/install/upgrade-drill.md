# Upgrade helper verification record

Verified locally on 2026-11-01 with Docker Engine 29.6.0, Compose v2 and
Postgres 16. The focused checks and the isolated product drill are described
separately below. The drill uses fake tenant data and a separate Docker daemon;
it does not verify an existing installation or excluded deployment topologies.

## Repeatable checks that passed

From the repository, using a Python environment with the test dependencies:

```bash
TRELLUM_TEST_DB_NAME=test_trellum_cleanup168review python scripts/test.py local \
  apps/core/tests/test_deployment_helpers.py --workers 1
TRELLUM_TEST_DB_NAME=test_trellum_cleanup168review python scripts/test.py local \
  apps/core/tests/test_deployment_helpers.py::test_real_postgres_restore_and_failed_swap \
  --heavy --workers 1
bash -n scripts/upgrade.sh scripts/rollback.sh
DJANGO_SETTINGS_MODULE=trellum_portal.settings.test \
  TRELLUM_TEST_DB_NAME=test_trellum_cleanup168review \
  python manage.py makemigrations --check --dry-run
```

The ordinary suite checks paired default/custom references, both pins and the
backup manifest, runner pull/version failures before downtime, backup and
migration failures, incorrect serving version/schema, running-image mismatch,
conflicting exported pins, concurrent-operation locks, explicit legacy pairs and archive validation, corrupt backups, fatal
restore failures and refusal to retry an unconfirmed rollback. Docker and
Compose are replaced by deterministic command adapters in those ordinary tests.

A separate heavy process check verifies that a timeout terminates the Compose
client and its child processes. The wrapper was also exercised with host Python
deliberately unavailable: its containerized Docker/Compose client reached
preflight on a uniquely named empty deployment. This is transport evidence,
not an upgrade or worker-build drill.

The heavy database test uses a **new uniquely named** `postgres:16-alpine` container,
512 MiB RAM, one CPU, no published ports, disposable credentials and database
`test_trellum_cleanup168review`. It runs the actual helper's dump/restore methods;
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

## Isolated product drill

A fake-data v0.5.0 → v0.9.1 → v0.5.0 drill used a separate Docker 29.9.2
rootless daemon inside a disposable container capped at 4 GiB RAM, two CPUs
and 1,024 processes, with no host ports published. The worker received only
that daemon's socket, protecting other installations from sandbox orphan
sweeps. The outer container enforced the combined resource bounds; per-build
cgroup enforcement inside that daemon was not tested. This daemon served only
as test isolation.

The procedure is:

1. Start the old portal/runner pair, seed a fake organization/studio and a
   small Python report, add an uploaded-file sentinel, and run cleanup and
   backups until `manage.py doctor` passes.
2. Run `scripts/upgrade.sh v0.9.1 --yes`. Verify both immutable pins,
   `/api/version` (`0.9.1`, schema `ok`) and `doctor`. The test database's
   migration count changed from 141 to 155.
3. Queue the report through the worker. Inspect the sandbox at container
   creation and compare its actual image ID with the selected runner ID.
   Run `b89f03b5-6ff1-42d3-b246-21f08d871db7` succeeded with exit code 0;
   its inspected sandbox image was
   `sha256:22ba80222ed761da302a93f86ff8c637a6455b5504447b65b42f7344a23f8805`,
   exactly the new runner pin on this daemon.
4. Add post-upgrade database sentinels, then run the printed manifest rollback
   command. Verify the old pair, serving schema/version, original data and
   uploaded-file sentinel. Inspect the retained post-upgrade database too.
   The completed rollback reported v0.5.0/schema `ok`, restored 141 migration
   records and the original organization name, and removed the newer sentinel
   table/run from the current database. The retained database still had 155
   migration records, both post-upgrade sentinels and the successful run. The
   uploaded-file sentinel was unchanged.

A migration-admission failure was also recovered through the manifest path.
The helper preserved its verified dump and left writers stopped before
recovery. Scratch-restore and failed transactional-swap behavior are covered
by the separate real Postgres test above.

## Deployment scope

External Postgres, offline/mirror-disconnected deployment, release-only
overlays, split/multi-host workers and arbitrary custom mounts are excluded
from these helpers' supported path. Protect uploads, encryption keys and
object storage separately. Repeat the drill on a disposable copy of your
own deployment before changing a working installation.
