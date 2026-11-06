# Validate report changes in CI

Run a build on each pull request to catch report errors before merging. The
existing `--fail-on-validation` option exits unsuccessfully when validation finds
**FAIL-level** issues. WARN and INFO findings remain visible but do not fail this
gate. Generator/query exceptions also fail the build.

## Prepare the project

Run from the analytics project root containing `reports/`. Commit a
`requirements-ci.txt` with a pinned Trellum version and every dependency your
report generators need. For example, start with `trellum==0.9.5` and use the
version tested by your team; pin any additional packages too.

CI executes your generators and reads their data. Use synthetic fixtures or a
disposable test database for pull requests. The workflow below assumes those
sources are already available without production credentials. Check that the
build log lists the reports you intend to validate; an empty or misplaced
`reports/` directory is not evidence that your reports passed.

Try the same command locally first:

```bash
python -m pip install -r requirements-ci.txt
python -m trellum.run --all --no-serve --no-cache --fail-on-validation --max-concurrent 2
```

`--no-cache` prevents reused query results from hiding source changes. The build
writes local output and exits without starting a preview. Do not add
`--production` for this gate: production mode also selects the publishing path
and requires an output backend. Do not add `--no-fail-on-validation` or ignore the
command's exit status.

## GitHub Actions example

Save this as `.github/workflows/validate-reports.yml` in your analytics repository:

```yaml
name: Validate reports

on:
  pull_request:
  push:
    branches: [main]

permissions:
  contents: read

concurrency:
  group: validate-reports-${{ github.ref }}
  cancel-in-progress: true

jobs:
  validate:
    name: Validate reports
    runs-on: ubuntu-latest
    timeout-minutes: 15
    env:
      FW_PROJECT_ROOT: ${{ github.workspace }}
    steps:
      - uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262 # v4.4.0
        with:
          persist-credentials: false
      - uses: actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065 # v5.6.0
        with:
          python-version: '3.12'
      - name: Install report dependencies
        run: python -m pip install -r requirements-ci.txt
      - name: Build and validate every report
        run: python -m trellum.run --all --no-serve --no-cache --fail-on-validation --max-concurrent 2
```

Replace `main` if your default branch differs. For a nested project, set both
`defaults.run.working-directory` and `FW_PROJECT_ROOT` to that project directory;
keep its `requirements-ci.txt` alongside `reports/`. Adjust the job timeout and
concurrency for your test workload. Maintain the action pins with your dependency
update process.

If a trusted CI job needs test-source credentials, provide the environment names
referenced by `data-sources/config.yaml` through GitHub Actions secrets. Portal
credentials are not automatically available in a checkout. Fork pull requests do
not receive repository secrets: keep their validation fixture-based. Do not use
`pull_request_target` to execute untrusted report code with secrets.

Make **Validate reports** a required status check in your repository's branch
protection or ruleset if failures should prevent merging. The workflow itself
reports failure; it does not configure that merge policy.

## Read a failed build

The log names the failing report and its validation findings or exception. Fix
the source and rerun the same command. When validation aborts generation, its
`_validation.json` and `_details.json` are written into that report's output
directory. A failure earlier in loading or querying may not produce these files.
Review generated output before sharing it: reports and logs can contain data.

This gate verifies generation and FAIL-level validation for the reports it
builds. It does not replace browser interaction tests, data-quality assertions,
or checks of a deployed portal's permissions. See
[Build failures](/docs/latest/troubleshooting/build-failures/) and
[The analytics repository](/docs/latest/workflow/analytics-repository/).
