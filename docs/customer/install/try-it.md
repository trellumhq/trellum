# Try Trellum locally

This is an evaluation path. For a production portal, start with [Choose how to
run Trellum](/docs/latest/install/overview/).

Trellum has two ways to try it. The Python framework builds a portable report
without a server or portal. The repository also includes a Docker demo of the
optional self-hosted portal. Both use generated sample data; neither needs a
warehouse account.

## Build a report with the Python package

Install Python 3.11 or newer, make a project directory, and create a virtual
environment. On macOS or Linux:

```bash
mkdir trellum-project && cd trellum-project
python3 -m venv .venv
source .venv/bin/activate
python -m pip install trellum
```

On Windows PowerShell:

```powershell
mkdir trellum-project
cd trellum-project
py -3 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install trellum
```

Install the example reports and their generated SQLite data, then build one
report:

```text
python -m trellum.demo --dest trellum-demo
cd trellum-demo
python -m trellum.run reports/store-health --no-serve --portable
python -m trellum.run reports/checkout-findings --no-serve --portable
python -m trellum serve --background
```

Open the URL printed by `serve`. The reports are in `output/`; portable output
can be copied to a static web host. The demo includes the written analysis
[Where Northwind loses buyers](https://trellum.dev/demo/checkout-findings/),
which builds from its committed article and evidence without querying the
warehouse. If you only want to try that article, install the demo files without
generating sample data:

```bash
python -m trellum.demo --dest trellum-analysis-demo --no-data
cd trellum-analysis-demo
python -m trellum.run reports/checkout-findings --no-serve --portable
python -m trellum serve --background
```

To create a project without the examples, run `python -m trellum.init`, then
create reports with `python -m trellum.new <name>`.

### Make your first change from the report

Ask your coding agent to start a live review, then select a chart or type a
request in the local preview:

> Start a live review of reports/store-health. Keep listening for my browser
> feedback, update and rebuild the report, and reply in the review panel.

Follow [Refine reports in your browser](../workflow/refine-reports.md) for the
full loop.

The demo command keeps files that already exist. In a Git project, merge these
patterns into its `.gitignore` before committing generated files; if the file
already exists, add the patterns instead of replacing it:

```gitignore
.env
.venv/
output/
data-sources/*.sqlite
```

The complete component and authoring reference is in the
[framework README](https://github.com/trellumhq/trellum/tree/main/trellum).

## Try the self-hosted portal

This demo requires Docker Desktop or Docker Engine with the Compose plugin. It
builds the portal and report-runner images from the checkout, creates a local
organization, and loads sample reports. It is for a local evaluation only: it
uses throwaway secrets and a known demo password. Do not expose it to the
internet or use it for real data.

Clone the released source on macOS or Linux:

```bash
TRELLUM_VERSION=v0.4.2
git clone --depth 1 --branch "$TRELLUM_VERSION" https://github.com/trellumhq/trellum.git
cd trellum
./scripts/demo.sh
```

On Windows PowerShell:

```powershell
$env:TRELLUM_VERSION = 'v0.4.2'
git clone --depth 1 --branch $env:TRELLUM_VERSION https://github.com/trellumhq/trellum.git
cd trellum
.\scripts\demo.ps1
```

Wait for the script to print the local URL and sign-in details, then open them
in a browser. Once the builds finish, open **Analyses → Where Northwind loses
buyers** to view the worked article and its captured evidence.

Remove the demo and its containers and data when finished:

```bash
./scripts/demo.sh --down
```

```powershell
.\scripts\demo.ps1 -Down
```

For a real deployment, use the [installation overview](/docs/latest/install/overview/)
and the [Docker Compose guide](/docs/latest/install/docker-compose/). The demo
scripts are not an installation shortcut for production.
