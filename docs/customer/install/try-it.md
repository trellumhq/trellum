# Try Trellum locally

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
python -m trellum serve --background
```

Open the URL printed by `serve`. The report is in `output/store-health/`;
the portable output can be copied to a static web host. To create a project
without the examples, run `python -m trellum.init`, then create reports with
`python -m trellum.new <name>`.

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
git clone --depth 1 --branch v0.3.0 https://github.com/trellumhq/trellum.git
cd trellum
./scripts/demo.sh
```

On Windows PowerShell:

```powershell
git clone --depth 1 --branch v0.3.0 https://github.com/trellumhq/trellum.git
cd trellum
.\scripts\demo.ps1
```

Wait for the script to print the local URL and sign-in details, then open them
in a browser. Remove the demo and its containers and data when finished:

```bash
./scripts/demo.sh --down
```

```powershell
.\scripts\demo.ps1 -Down
```

For a real deployment, use the [installation overview](/docs/latest/install/overview/)
and the [Docker Compose guide](/docs/latest/install/docker-compose/). The demo
scripts are not an installation shortcut for production.
