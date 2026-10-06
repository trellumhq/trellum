"""Entry point for: python -m trellum

The console script (`trellum`) only exists on PATH when the virtualenv is
activated, and agents do not activate virtualenvs -- they invoke the interpreter
by path: `.venv/Scripts/python.exe -m trellum.run`. A benchmark run confirmed
the cost of ignoring that: the CLI shipped, the agent read the guide that
advertised it, and then never invoked it once, because the only documented form
was unreachable.

So the agent-facing entry point is available the same way as every other one:

    python -m trellum            what this is, and what to ask for
    python -m trellum.run        build a report
    python -m trellum.new        scaffold a report
    python -m trellum.demo       install the demo project
    python -m trellum.init       initialise a project
"""

from trellum.cli import main

raise SystemExit(main())
