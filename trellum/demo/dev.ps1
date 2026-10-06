<#
.SYNOPSIS
    Dev entry point for the demo project.

.DESCRIPTION
    Wraps the common loop so you never have to think about virtualenvs or PATH.
    Every command uses the repo's .venv interpreter directly, creating it on
    first use, so there is nothing to activate.

.EXAMPLE
    .\dev.ps1 setup
    .\dev.ps1 list
    .\dev.ps1 run player-overview
    .\dev.ps1 serve
#>

[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [string]$Command = "help",

    [Parameter(Position = 1, ValueFromRemainingArguments = $true)]
    [string[]]$Rest = @()
)

$ErrorActionPreference = "Stop"

$DemoDir  = $PSScriptRoot
$RepoRoot = Split-Path $DemoDir -Parent
$VenvDir  = Join-Path $RepoRoot ".venv"
$Py       = Join-Path $VenvDir "Scripts\python.exe"

# The demo's clock is pinned to the last day of its own data, for every command
# that builds a report -- not just the baseline ones.
#
# Fixtures end at a fixed anchor. Left on the wall clock, anything a report
# derives from "today" lands past the end of the data and comes back empty:
# player-overview's Yesterday section, and portfolio-health's period-over-period
# cards, which showed $0.00 and -100% against a full prior period. Nothing looks
# broken -- the queries succeed and return nothing -- so it reads as the
# framework being wrong rather than the clock being in the wrong place.
#
# Read from the database rather than written down here. A constant would have to
# be kept in step with whatever anchor the fixtures were actually built at, and
# the failure when it drifts is silent in exactly the same way.
function Get-FixtureAnchor {
    $anchor = & $Py (Join-Path $DemoDir "tools\make_fixtures.py") --show-anchor
    if ($LASTEXITCODE -ne 0 -or -not $anchor) {
        throw "Could not read the fixture anchor. Try: .\dev.ps1 fixtures"
    }
    return $anchor.Trim()
}

function Sync-DemoClock {
    $env:FW_NOW = Get-FixtureAnchor
    Write-Host "  Clock pinned to FW_NOW=$($env:FW_NOW) (last day of fixture data)."
}

$ServePort  = 8050   # framework's own multi-report server

# The framework default is 5%, which is useless here: a full-page screenshot is
# ~1400x3800, so even replacing a whole chart title only moves 0.08% of pixels.
# Measured on this suite, with animations frozen during capture:
#
#   identical re-run ......... 0.00%
#   one word in a title ...... 0.01%
#   whole title replaced ..... 0.08%
#
# 0.005% catches the smallest of those with headroom and produced no false
# positives over repeated runs. Raise it if you hit font-rendering differences
# on another machine.
$BaselineThreshold = "0.005"

function Add-ThresholdDefault([string[]]$argv) {
    if ($argv -contains "--baseline-threshold") { return $argv }
    return $argv + @("--baseline-threshold", $BaselineThreshold)
}

function Write-Step($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }
function Write-Warn($msg) { Write-Host "  ! $msg" -ForegroundColor Yellow }

function Get-Bootstrap {
    # The `py` launcher is the reliable way to find Python on Windows --
    # `python` is usually shadowed by the Microsoft Store alias.
    foreach ($c in @("py", "python3", "python")) {
        $cmd = Get-Command $c -ErrorAction SilentlyContinue
        if ($cmd) {
            # The Store alias is a 0-byte stub that only opens the Store.
            if ($cmd.Source -and $cmd.Source -like "*WindowsApps*") { continue }
            return $cmd.Source
        }
    }
    throw "No Python found. Install Python 3.11+ from https://python.org and re-run: .\dev.ps1 setup"
}

function Initialize-Venv {
    if (Test-Path $Py) { return }
    Write-Step "Creating virtualenv at .venv (first run)"
    $bootstrap = Get-Bootstrap
    & $bootstrap -m venv $VenvDir
    if ($LASTEXITCODE -ne 0) { throw "Failed to create the virtualenv." }
    Write-Step "Installing dependencies"
    & $Py -m pip install --quiet --upgrade pip
    & $Py -m pip install --quiet -r (Join-Path $RepoRoot "requirements.txt")
    if ($LASTEXITCODE -ne 0) { throw "Dependency install failed." }
    Write-Host "  Virtualenv ready." -ForegroundColor Green
}

function Initialize-Fixtures {
    # Two jobs, because they are the same job: make the data right, then put the
    # clock where the data is.
    #
    # Not just "does the file exist". A database built before a table was added
    # to make_fixtures.py exists and is wrong, and the report that queries the
    # missing table fails with `no such table`, which reads as a bug in the
    # report. --ensure decides, and reuses the anchor and seed already recorded
    # in the file so a pinned dataset keeps its numbers.
    & $Py (Join-Path $DemoDir "tools\make_fixtures.py") --ensure
    if ($LASTEXITCODE -ne 0) { throw "Fixture generation failed." }
    Sync-DemoClock
}

function Test-PortOpen([int]$port) {
    try {
        $client = New-Object System.Net.Sockets.TcpClient
        $async = $client.BeginConnect("127.0.0.1", $port, $null, $null)
        $ok = $async.AsyncWaitHandle.WaitOne(300)
        if ($ok) { $client.EndConnect($async); $client.Close(); return $true }
        $client.Close(); return $false
    } catch { return $false }
}

function Show-ReportLinks {
    # Read the slugs off disk rather than assuming: this reflects what was
    # actually built, so a filtered run (--category X) lists only those.
    $outDir = Join-Path $DemoDir "output"
    if (-not (Test-Path $outDir)) { return }
    $built = Get-ChildItem $outDir -Directory -ErrorAction SilentlyContinue |
             Where-Object { Test-Path (Join-Path $_.FullName "index.html") } |
             Sort-Object Name
    if (-not $built) { return }

    # Report the server if it is actually up.
    $port = $null; $via = ""
    if     (Test-PortOpen $ServePort)  { $port = $ServePort;  $via = "framework server" }

    Write-Host ""
    if ($port) {
        Write-Host "Live on the $via -- open any of these:" -ForegroundColor Green
    } else {
        $port = $ServePort
        Write-Host "Built. Serve them with:  " -NoNewline
        Write-Host ".\dev.ps1 serve" -ForegroundColor Cyan
    }
    Write-Host ""
    Write-Host ("  {0,-22} http://localhost:{1}/" -f "index", $port)
    foreach ($r in $built) {
        Write-Host ("  {0,-22} http://localhost:{1}/{2}/index.html" -f $r.Name, $port, $r.Name)
    }
    if ($via -eq "") {
        Write-Host ""
        Write-Host "  (links go live once a server is running)" -ForegroundColor DarkGray
    }
}

function Invoke-Py { param([string[]]$PyArgs)
    # Deliberately returns nothing: the child's output must flow straight to
    # the console rather than becoming this function's return value. Callers
    # read $LASTEXITCODE, which the native command sets globally.
    Push-Location $DemoDir
    try { & $Py @PyArgs }
    finally { Pop-Location }
}

function Show-Help {
    @"
Demo project dev commands

  .\dev.ps1 setup                 Create the virtualenv and install dependencies
  .\dev.ps1 fixtures [args]       Regenerate the demo database
                                    e.g. fixtures --small
                                         fixtures --anchor 2026-06-30 --seed 42
  .\dev.ps1 list                  List reports and their last run status
  .\dev.ps1 run <slug> [args]     Build a report and serve it on :8050
                                    e.g. run player-overview
                                         run monetization --port 8060
                                         run player-overview --no-serve
  .\dev.ps1 all [args]            Run EVERY report against the fixtures
                                    (aliases: build, run-all, run all)
                                    e.g. all --max-concurrent 3
                                         all --category Revenue
  .\dev.ps1 serve [port]          Run every report AND serve them all on one
                                    port with an index (default :8050).
  .\dev.ps1 test [args]           Run every report against mock data (no database)
  .\dev.ps1 clean                 Remove generated output and the query cache

Authoring
  .\dev.ps1 new <slug> [opts]     Scaffold a new report from _template
                                    -Title "Human Name"  -Category Revenue
                                    -Theme ocean         -Run (build it after)

Regression pipeline
  .\dev.ps1 baseline              Capture visual baselines (pinned clock)
  .\dev.ps1 check                 Compare rendered output to the baselines
  .\dev.ps1 ci                    build -> validate -> visual check, in one go

The virtualenv is created automatically on first use -- there is nothing to
activate. To use it in your own shell: .\.venv\Scripts\Activate.ps1
"@ | Write-Host
}

switch ($Command.ToLower()) {

    "setup" {
        Initialize-Venv
        Write-Step "Generating fixtures"
        Initialize-Fixtures
        Write-Host ""
        Write-Host "Ready." -ForegroundColor Green
        Write-Host "  .\dev.ps1 run player-overview   one report, served on :8050"
        Write-Host "  .\dev.ps1 all                   every report"
        exit 0
    }

    "fixtures" {
        Initialize-Venv
        Invoke-Py (@("tools\make_fixtures.py") + $Rest)
        exit $LASTEXITCODE
    }

    { $_ -in "list", "ls" } {
        Initialize-Venv
        Invoke-Py (@("tools\list_reports.py") + $Rest)
        exit $LASTEXITCODE
    }

    "run" {
        Initialize-Venv
        if (-not $Rest -or $Rest.Count -eq 0) {
            Write-Warn "Which report? Pick one:"
            Write-Host ""
            Invoke-Py @("tools\list_reports.py")
            exit 1
        }
        Initialize-Fixtures
        # "run all" is the same thing as "build" -- accept both phrasings.
        if ($Rest[0] -in @("all", "--all")) {
            $extraAll = if ($Rest.Count -gt 1) { $Rest[1..($Rest.Count - 1)] } else { @() }
            Invoke-Py (@("-m", "trellum.run", "--all") + $extraAll)
            $code = $LASTEXITCODE
            if ($code -eq 0) { Show-ReportLinks }
            exit $code
        }
        # Accept either "player-overview" or "reports\player-overview".
        $slug = $Rest[0]
        $target = if ($slug -match "[\\/]") { $slug } else { "reports\$slug" }
        if (-not (Test-Path (Join-Path $DemoDir $target))) {
            Write-Warn "No report at $target"
            Write-Host ""
            Invoke-Py @("tools\list_reports.py")
            exit 1
        }
        $extra = if ($Rest.Count -gt 1) { $Rest[1..($Rest.Count - 1)] } else { @() }
        Invoke-Py (@("-m", "trellum.run", $target) + $extra)
        exit $LASTEXITCODE
    }

    { $_ -in "build", "all", "run-all" } {
        Initialize-Venv
        Initialize-Fixtures
        Invoke-Py (@("-m", "trellum.run", "--all") + $Rest)
        $code = $LASTEXITCODE
        if ($code -eq 0) { Show-ReportLinks }
        exit $code
    }

    "test" {
        Initialize-Venv
        # Mock mode patches out every connection, so no fixtures needed.
        Invoke-Py (@("-m", "trellum.run", "--all", "--test") + $Rest)
        exit $LASTEXITCODE
    }

    "baseline" {
        Initialize-Venv
        Initialize-Fixtures
        Write-Step "Capturing baselines at FW_NOW=$($env:FW_NOW)"
        Invoke-Py (@("-m", "trellum.run", "--all", "--test",
                     "--test-screenshot", "--save-baseline") + $Rest)
        $code = $LASTEXITCODE
        Remove-Item Env:\FW_NOW -ErrorAction SilentlyContinue
        if ($code -eq 0) {
            Write-Host "  Baselines written to ..\testing\baselines\" -ForegroundColor Green
        }
        exit $code
    }

    "check" {
        Initialize-Venv
        $baselineDir = Join-Path $RepoRoot "testing\baselines"
        if (-not (Test-Path $baselineDir)) {
            Write-Warn "No baselines yet. Capture them first: .\dev.ps1 baseline"
            exit 1
        }
        Initialize-Fixtures
        Write-Step "Comparing against baselines at FW_NOW=$($env:FW_NOW)"
        $a = Add-ThresholdDefault (@("-m", "trellum.run", "--all", "--test",
                                     "--test-screenshot", "--check-baseline") + $Rest)
        Invoke-Py $a
        $code = $LASTEXITCODE
        Remove-Item Env:\FW_NOW -ErrorAction SilentlyContinue
        exit $code
    }

    "ci" {
        # The full gate: build against real fixtures, then validate against
        # mock data, then compare rendered output to the baselines.
        Initialize-Venv
        Initialize-Fixtures
        Write-Step "1/3  Building all reports against fixtures"
        Invoke-Py @("-m", "trellum.run", "--all")
        if ($LASTEXITCODE -ne 0) { Write-Warn "build failed"; exit $LASTEXITCODE }

        Write-Step "2/3  Validating against mock data (no database)"
        Invoke-Py @("-m", "trellum.run", "--all", "--test")
        if ($LASTEXITCODE -ne 0) { Write-Warn "mock run failed"; exit $LASTEXITCODE }

        $baselineDir = Join-Path $RepoRoot "testing\baselines"
        if (-not (Test-Path $baselineDir)) {
            Write-Warn "3/3  Skipped visual check -- no baselines. Run: .\dev.ps1 baseline"
            exit 0
        }
        # Clock already pinned by Initialize-Fixtures at step 1.
        Write-Step "3/3  Visual comparison against baselines"
        Invoke-Py (Add-ThresholdDefault @("-m", "trellum.run", "--all", "--test",
                                          "--test-screenshot", "--check-baseline"))
        $code = $LASTEXITCODE
        Remove-Item Env:\FW_NOW -ErrorAction SilentlyContinue
        if ($code -eq 0) { Write-Host "`nAll checks passed." -ForegroundColor Green }
        exit $code
    }

    "serve" {
        # Builds everything, then serves
        # the whole output directory with a generated index.
        Initialize-Venv
        Initialize-Fixtures
        $port = if ($Rest -and $Rest.Count -gt 0) { $Rest[0] } else { "$ServePort" }
        Invoke-Py @("-m", "trellum.run", "--all", "--serve", "--port", $port)
        exit $LASTEXITCODE
    }

    "new" {
        Initialize-Venv
        if (-not $Rest -or $Rest.Count -eq 0) {
            Write-Warn "Usage: .\dev.ps1 new <slug> [-Title ""Name""] [-Category X] [-Theme y] [-Run]"
            exit 1
        }

        # Slug: lowercase, spaces/underscores to hyphens.
        $slug = ($Rest[0].ToLower() -replace '[\s_]+', '-') -replace '[^a-z0-9-]', ''
        if (-not $slug) { Write-Warn "'$($Rest[0])' is not a usable slug."; exit 1 }

        # Parse the optional flags out of the remaining arguments.
        $title = ""; $category = "Uncategorized"; $themeName = "ocean"; $runAfter = $false
        for ($i = 1; $i -lt $Rest.Count; $i++) {
            switch -Regex ($Rest[$i]) {
                '^-{1,2}title$'    { $i++; $title = $Rest[$i] }
                '^-{1,2}category$' { $i++; $category = $Rest[$i] }
                '^-{1,2}theme$'    { $i++; $themeName = $Rest[$i] }
                '^-{1,2}run$'      { $runAfter = $true }
                default { Write-Warn "ignoring unknown option: $($Rest[$i])" }
            }
        }
        if (-not $title) {
            $title = (Get-Culture).TextInfo.ToTitleCase(($slug -replace '-', ' '))
        }
        # PascalCase class name, e.g. daily-revenue -> DailyRevenueReport
        $class = (($slug -split '-') | ForEach-Object {
            if ($_) { $_.Substring(0,1).ToUpper() + $_.Substring(1) }
        }) -join ''

        $src  = Join-Path $DemoDir "reports\_template"
        $dest = Join-Path $DemoDir "reports\$slug"
        if (Test-Path $dest) { Write-Warn "reports\$slug already exists."; exit 1 }
        if (-not (Test-Path $src)) { Write-Warn "reports\_template is missing."; exit 1 }

        Copy-Item -Recurse $src $dest
        Remove-Item -Recurse -Force (Join-Path $dest "__pycache__") -EA SilentlyContinue

        # report.yaml: identity, classification and theme.
        $yamlPath = Join-Path $dest "report.yaml"
        $yaml = Get-Content $yamlPath -Raw
        $yaml = $yaml -replace '(?m)^name:.*$',        "name: `"$title`""
        $yaml = $yaml -replace '(?m)^slug:.*$',        "slug: $slug"
        $yaml = $yaml -replace '(?m)^description:.*$', "description: `"TODO: describe $title in a sentence -- under 60 characters raises a validation warning.`""
        $yaml = $yaml -replace '(?m)^category:.*$',    "category: $category"
        # Drop the template's leading explanatory comment block.
        $yaml = $yaml -replace '(?s)^# Copy this directory.*?\r?\n\r?\n', ''
        if ($yaml -match '(?m)^theme:') { $yaml = $yaml -replace '(?m)^theme:.*$', "theme: $themeName" }
        else { $yaml = "theme: $themeName`r`n" + $yaml }
        Set-Content $yamlPath $yaml -Encoding UTF8

        # generator.py: class name and docstring.
        $genPath = Join-Path $dest "generator.py"
        $gen = Get-Content $genPath -Raw
        $gen = $gen -replace 'class TemplateReport\(BaseReport\):', "class ${class}Report(BaseReport):"
        $gen = $gen -replace '(?s)^""".*?"""', "`"`"`"$title.`n`nTODO: describe what this report answers.`n`"`"`""
        Set-Content $genPath $gen -Encoding UTF8

        # queries.py: retitle the docstring.
        $qPath = Join-Path $dest "queries.py"
        $q = Get-Content $qPath -Raw
        $q = $q -replace 'SQL queries for the template report\.', "SQL queries for $slug."
        Set-Content $qPath $q -Encoding UTF8

        Write-Host "  Created reports\$slug" -ForegroundColor Green
        Get-ChildItem $dest -File | ForEach-Object { Write-Host "    $($_.Name)" }

        if ($runAfter) {
            Initialize-Fixtures
            Invoke-Py @("-m", "trellum.run", "reports\$slug", "--no-serve")
            exit $LASTEXITCODE
        }
        Write-Host ""
        Write-Host "Next:  .\dev.ps1 run $slug" -ForegroundColor Cyan
        exit 0
    }

    "clean" {
        foreach ($p in @("output", "output\.query_cache")) {
            $full = Join-Path $DemoDir $p
            if (Test-Path $full) { Remove-Item -Recurse -Force $full; Write-Host "  removed $p" }
        }
        Write-Host "Clean. Fixtures kept -- delete data-sources\demo.sqlite to drop those too."
        exit 0
    }

    default {
        if ($Command -and $Command -notin @("help", "-h", "--help")) {
            Write-Warn "Unknown command: $Command"
            Write-Host ""
        }
        Show-Help
        exit 0
    }
}
