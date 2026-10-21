# Local review response timing

Run from the repository root with the Python environment used for the browser
tests (Playwright and its Chromium must already be available):

```sh
python scripts/review_response_timing.py --samples 3 --rebuild \
  --deadline 180 --output /tmp/review-response-baseline.json
```

This opt-in diagnostic builds two tiny synthetic fixtures in a temporary project,
serves the production local review handlers and injected widget, and sends real
Chromium chat messages through standalone report review and the analysis
workspace. It never calls a model or configured datasource. Browser requests
outside the local fixture server are blocked. It does not modify product UI.
The deterministic synthetic agent polls, submits activity and replies through
the real HTTP endpoints. This is local protocol timing, not provider timing.

Each surface has `--samples` repetitions (1–10) of both an agent already parked
in a long poll and a synthetic busy agent that starts polling `--busy-ms`
(default 600, maximum 5000) after acknowledgement. The latter is an imposed
availability delay, not measured model work. A listening status observation
confirms the parked case before sending. The whole worker process group,
including Chromium, is terminated on the deadline (10–600 seconds; up to five
additional seconds for termination). HTTP reads, browser observations and agent
futures also have finite timeouts. A failure exits unsuccessfully without
writing a successful result; an older output file, if present, remains old.

JSON contains individual monotonic milestones, session/slug/feedback identity,
browser/Python version, source commit and median/max milliseconds per surface
and mode. Send begins before the browser click, acknowledgement follows reading
the feedback response, pickup follows reading the scoped poll response, and
activity submission precedes the activity POST. Visibility means the correlated
conversation row actually displays the activity or completed state, with reply
text also visible. The harness waits for activity visibility before replying so
that a rapid completion cannot hide that measurement. This deliberately adds a
wait between activity and reply; do not interpret the total round as model time.
Real animation-frame observations avoid locator retry backoff. Playwright
observation and click overhead are included. `ack_to_pickup` can be
negative when a parked poll wins the race with browser acknowledgement; retain
that signed result, and use `send_to_pickup` alongside it. Submission-to-visible
and acknowledgement-to-visible metrics distinguish the HTTP cost from display
lag. Initial builds and optional simple rebuilds are recorded separately, without
a browser reload measurement or a warehouse query. No millisecond pass/fail
threshold is used.

The protocol smoke test is marked heavy, following the repository timing and
real-process test policy:

```sh
python scripts/test.py local scripts/test_review_response_timing.py --heavy --workers 1
```

For an external editor/provider investigation, repeat the same small change on
both surfaces with the actual agent. Keep a scoped
`python -m trellum review poll --report <slug> --timeout 120 --json` running
before sending. Record the poll result's original `session`, `slug` and
`feedback_id`; pass them to `review activity` and `review reply`. Never complete
old work using a newly looked-up session. Use browser network timing for the
feedback acknowledgement and subsequent status/workspace-state responses,
terminal monotonic timestamps for poll return, activity and reply commands,
and a screen recording or DOM observation for visible activity and completion.
Also record when the editor dispatches the poll tool, receives its result,
starts the next tool and finishes generation. Correlate events by feedback
identity rather than chat text alone. Keep raw token-bearing workspace URLs,
headers and credentials out of shared logs. Cross-machine timestamps need clock
alignment; prefer durations within one clock domain.

The existing wait sources are inspectable in `trellum/static/js/review/state.js`
(`STATUS_MS = 2000`), `status.js` (periodic status reads), and `workspace.js`
(`setInterval(pollState,2500)`). Hidden tabs pause those reads. In
`trellum/cli/commands/review.py`, each CLI request parks for up to 30 seconds
and immediately reissues on a waiting result; `ReviewState.poll` wakes on queue
notification. Thirty seconds is a maximum idle park, not a fixed pickup delay.
The guide in `trellum/AGENTS.md` requires keeping that poll active and rebuilding
before replying. An editor that stops polling while busy adds its own pickup
gap. Measure that dispatch gap and provider generation separately before
attributing end-to-end slowness to the local server.
