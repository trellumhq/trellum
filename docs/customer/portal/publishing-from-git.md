# Publishing from git

Every sync does two things, and they are now separate: it **fetches** the
branch and works out what has changed, then it **publishes** — copies that
commit into the studio, re-scans the registry, and rebuilds what changed.

Fetching always happens. Whether publishing follows is the studio's choice.

## The two modes

**Studio settings → Repository** carries a **Publishing** switch with two
positions:

| Mode | Behaviour |
|---|---|
| **Automatically** | Every push to the branch goes live as soon as the runner sees it. This is the default and the original behaviour. |
| **Manually** | The portal fetches and shows what changed. Nothing goes live until someone presses **Publish**. |

Under the switch, the page states whichever one is in force:

> Every push to `main` goes live as soon as the runner sees it.

> The portal fetches and shows what changed; nothing goes live until you press
> Publish.

Choose a mode, then press **Save changes** to apply it. Saving
**Automatically** while commits are waiting publishes them, so the portal
confirms first:

> **Switch to automatic publishing?** This publishes the 3 pending commits now.

Switching to **Manually** never rolls anything back. The studio stays on the
commit it is serving; the next push simply waits for you.

Both modes keep a full publish history, and both fetch on the same schedule.

## What the top of the page tells you

Two columns, side by side:

- **Published** — the commit the studio is serving, how long ago it was
  published, who published it and what triggered it. Below it, an **ok**
  badge, a **sync scheduled** badge, or an **error** badge with the message.
- **Remote `<branch>`** — the head the last fetch found and how long ago it
  was checked, then either **Up to date** or **3 commits ahead**. If the
  runner has missed two polls in a row, the "checked …" text is highlighted:
  the remote column is stale, not the branch.

The buttons underneath change with the mode. In **Automatically** it is
**Sync now**. In **Manually** it is **Check for changes**, next to **Review &
publish** — which reads **Up to date** and is disabled when there is nothing
pending, and **Publishing…** while a request is in flight. Either button
schedules the work; the runner picks it up within seconds.

## The pending panel

In manual mode the page always shows a **Pending changes** panel, headed with
the number of commits. Two variants replace that heading:

- **Initial import** — the first publish for this studio. Every report reads
  as added.
- **History was rewritten — showing the full contents of `<branch>`** — the
  branch was force-pushed. The panel lists the whole branch rather than a
  range, because there is no longer a straight line from the published commit
  to this one.

Inside, a row of chips summarises the change set — only the non-zero ones
appear:

> 4 reports modified · 1 report added · 1 report removed · 1 data source added ⚠
> · 2 data sources changed · events.yaml changed

Then the warnings that are worth reading before you publish:

- **A data source that will not be ready.** *Adds data source `warehouse` —
  not configured yet. Reports that use it will wait for it after publishing.*
  A **Configure now** button goes straight to the credential form, so you can
  have it connected before the reports that need it arrive. See
  [Connecting your data sources](/docs/latest/portal/connecting-your-data-sources/).
- **A report being removed.** *Removes `old-report` — its page will show it
  was removed from the repository; built output is kept.*
- **A quota that will bite.** *2 report(s) will not be registered: this
  organization is limited to 20 reports.*
- **A file the portal could not read**, such as a `data-sources/config.yaml`
  that is not valid YAML. The parser's problem and line are shown; the rest of
  the publish is unaffected.

Below that, the commits themselves: hash, message, author, when, and the number
of files each one touched (hover for the list). The latest 100 are listed, with
a line saying so when the branch is further ahead than that.

The same chips and a short list of consequences reappear in the confirmation
dialog, so what you approve is what you reviewed.

## Publishing

**Review & publish** opens a dialog headed **Publish 3 commits to `<studio>`?**
It re-checks the remote as it opens, and **Publish** stays disabled until that
check confirms the branch is still on the commit the page is showing. If
someone pushed in the meantime you get *New commits arrived — review again*
instead of a publish; close the dialog and reload.

When there are reports to rebuild, the dialog carries a checkbox —
**Rebuild the 4 changed reports after publishing** — pre-ticked from the
studio's own *Rebuild reports changed by a push* setting. It applies to this
publish only; the studio's setting is unchanged.

Pressing **Publish** hands the request to the runner, which then:

1. Checks the commit out into the studio's cached checkout.
2. **Validates every `report.yaml` in it.** A broken one stops the publish
   here, before anything reaches the studio.
3. Copies the changed reports and any project-root files (`events.yaml`,
   `metrics.yaml`, `config.yaml`) into the studio's working copy.
4. Re-scans the report registry.
5. Mirrors the data sources the commit declares.
6. Queues rebuilds of the added and modified reports, if that was asked for.

### The commit you approved is the commit that publishes

A publish request names the commit it reviewed. If the branch moves before the
runner gets to it, the request is dropped rather than publishing something
nobody looked at, and the repository page says:

> The remote moved since you reviewed it — review and publish again.

The new commits are already in the pending panel; review them and publish
again.

### When a publish fails

A failed publish changes nothing. The studio keeps serving the commit it was
already on, `report.yaml` files that were valid yesterday keep building, and
the failure is recorded in the history with its error.

The request is spent either way, so a persistent problem does not retry every
few seconds. That is also true in automatic mode: a commit that failed to
publish is not retried on its own. Push a fix — a new commit is a new change
set, and it publishes normally.

## History

Every publish attempt, in either mode, is recorded in the **History** table at
the bottom of the page:

| Column | Shows |
|---|---|
| **Date** | How long ago |
| **Range** | The commit published from → the commit published to |
| **By** | Who and what triggered it: a person's name · manual, `webhook · auto`, `schedule · auto`, or `initial` |
| **Commits** | Expandable — every commit in the change set |
| **Reports changed** | How many reports the change set touched |
| **Status** | **Published**, or **Failed** with the error that stopped it |

Twenty rows load at a time, up to two hundred.

## Webhooks and polling

A push webhook is the fastest way to pick up a change; polling is the fallback,
and the **Poll interval** field turns it off entirely at `0`. Both work in both
modes, but what they do differs:

- In **Automatically**, a webhook delivery fetches and publishes.
- In **Manually**, a webhook delivery only fetches. The pending panel updates,
  and the change set waits for a person. This is the point of the mode: the
  webhook keeps the portal current on what *would* change without changing
  anything.

Webhook setup — the URL, the secret, the signature header — is covered in
[Connecting a repository](/docs/latest/portal/connecting-a-repository/).

## Which mode should a studio use?

**Automatically** suits a repository where the branch is already the reviewed
state: pull requests are merged into `main` after review, and a merge is meant
to go live. Most studios stay here.

**Manually** suits a studio where publishing is its own decision — reports that
back a monthly cycle, a shared branch several people push to, or a period when
you want to see exactly what a change set does before anyone sees the reports.
It costs one click per release and gives you a reviewed, attributed record of
each one.

## Next

- [Connecting a repository](/docs/latest/portal/connecting-a-repository/)
- [Connecting your data sources](/docs/latest/portal/connecting-your-data-sources/)
- [Build failures](/docs/latest/troubleshooting/build-failures/)
