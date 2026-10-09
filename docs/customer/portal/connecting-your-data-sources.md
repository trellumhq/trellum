# Connecting your data sources

Your repository says **which** sources exist. The portal holds **the secrets**
for them. Neither half is complete on its own, and that split is the whole
design: a warehouse password never lands in a clone, a fork, or your git
history, and a report never has to be edited to change where it connects.

- **The repository declares.** `data-sources/config.yaml` (or an inline entry
  in a `report.yaml`) names each source and its non-secret connection details
  — type, host, port, database, file path.
- **The portal connects.** Studio settings → **Data sources** asks only for
  the credentials, stores them encrypted, and injects them into that studio's
  report builds and nothing else.

Every sync mirrors the declarations from the published commit, so the portal's
list of sources always matches the repository it is serving.

The portal supports PostgreSQL, MySQL, Vertica, ClickHouse, SQL Server,
Redshift, Trino, Databricks, Snowflake, BigQuery, Google Sheets, SQLite,
DuckDB, CSV/Excel files, image assets, and OneDrive/SharePoint. Available
credential fields depend on the source type; the Configure form shows the
fields for the selected source.

These built-in readers expose supported tabular sources to report code. Other
formats, including document files, need a Python reader or extraction step in
your project code that turns the relevant content into a pandas DataFrame;
declaring a file source alone does not parse arbitrary documents.

## Declare a source in the repository

The central file is `data-sources/config.yaml`, at the top of your project:

```yaml
# data-sources/config.yaml
sources:
  warehouse:
    type: postgres
    description: "Analytics warehouse"
    host: warehouse.internal
    port: 5432
    database: analytics
    credentials:
      local: BI_WAREHOUSE     # only used when you run locally

  snapshots:
    type: sqlite
    description: "Nightly snapshot extract"
    path: data-sources/files/snapshots.db
    upload: true              # the portal may replace this file
```

A report opts into the sources it reads:

```yaml
# reports/weekly-revenue/report.yaml
name: Weekly revenue
data_sources:
  - warehouse
  - snapshots
```

That is all the repository needs. The `credentials:` block is for running the
report on your own machine — the portal ignores it and uses what you enter in
the UI instead. Nothing in this file is secret, so committing it is safe and
useful: a colleague who clones the repository can see which sources exist.

!!! note
    A `report.yaml` may also declare a source inline, as a mapping with its own
    `name:`, instead of referencing one from the central file. That works and is
    mirrored the same way — the portal shows it as *declared in
    `<slug>/report.yaml`*. If both files declare the same name,
    `data-sources/config.yaml` wins.

Push the change, then [publish it](/docs/latest/portal/publishing-from-git/).
The source appears in the portal on the next sync.

## Connect it in the portal

**Studio settings → Data sources** lists every source your repository
declares, every source configured in the portal, and every source a report
references. Studio admins can open this page.

A newly declared source arrives as **Needs credentials**, and a banner at the
top of the page counts what is outstanding:

> **2 data sources still need credentials** — 5 reports are waiting for them.

Press **Configure** on the row. The form has two halves:

- **From repository** — read-only: type, host, port, database (or path),
  which file declared it, and which reports use it. These come from the
  commit; to change them, change the repository and publish.
- The credential fields for that type. Postgres asks for
  **User** and **Password**. BigQuery asks for **Credentials JSON**.
  Databricks asks for **Access token**. OneDrive/SharePoint asks for
  **Client secret**. Google Sheets asks for **Credentials JSON** too — the
  service account key, pasted as-is, so no key file has to be placed on the
  worker; leave it blank to keep using a declared `credentials_path` or the
  worker's default credentials. On a source you have already configured,
  leaving a secret field blank keeps the stored value.

**Save and test** stores the credentials encrypted and immediately makes a real
connection with a ten-second deadline. If it fails you get the driver's own
error with the credentials scrubbed, and the source stays where it was. If it
succeeds the page says so — and any report that was waiting on that source is
queued to build straight away:

> "warehouse" connected. 5 waiting reports have been queued to build.

**Test** on a row re-runs that check at any time. **Remove credentials** on the
Configure form clears them again; reports that read from the source go back to
waiting.

## Use a new connection for every query

For remote SQL sources, **Use a new connection for every query** is available
in the source's settings or Configure form. It is off by default. Enable it
when a source has unreliable persistent sessions and your report's queries
can run independently.

Each uncached framework query opens a connection, fetches the complete result,
and closes that connection. The next query uses another connection, even if the
previous one succeeded. Recognized transient failures still get bounded retries
on fresh connections. Cache hits do not open a connection. The setting does not
change Vertica's existing TLS-disabled, autocommit-enabled defaults.

Connection setup adds overhead. Queries cannot share temporary tables, session
settings or transactions in this mode. Report code must use `query_df` or `query`;
direct cursor methods and connection attribute access are rejected. Local
SQLite/DuckDB and file sources are unaffected.

A repository may declare `new_connection_per_query: true` in its datasource
entry. An explicit portal setting overrides that value for its binding, including
turning it off. Standalone runs can override it with
`<CREDENTIAL_PREFIX>_NEW_CONNECTION_PER_QUERY=true` or `false`.

Saving affects subsequent report runs. **Test connection** checks whether the
source can be reached; a successful check does not prove that every report query
will succeed.

Under the table, one line states the rule that catches people out:

> Repo-declared fields (type, host, port, database, path) are read-only here.
> Change them in the repository and publish.

## Reaching a database through an SSH bastion

A database that is only reachable from a bastion host declares the tunnel next
to its connection details:

```yaml
sources:
  warehouse:
    type: postgres
    host: 10.0.1.5                        # the database as the bastion sees it
    port: 5432
    database: analytics
    ssh_host: bastion.example.com
    ssh_user: analytics
    ssh_host_key: "ssh-ed25519 AAAA..."   # from `ssh-keyscan bastion.example.com`
```

`ssh_host`, `ssh_port` (default 22), `ssh_user` and `ssh_host_key` are
connection details like `host` and `port`: not secret, declared in the
repository, read-only in the portal. The Configure form then asks for the
bastion's credential as well as the database's -- an **SSH private key**
(paste the key file's contents) or an **SSH password** -- and stores it
encrypted with the rest. Every build, connection test and live query of that
source opens the tunnel itself and closes it with the connection; nothing on
the worker has to be set up.

`ssh_host_key` is the bastion's public host key, as `ssh-keyscan <host>`
prints it. With it set, a bastion presenting any other key is refused before
the credential is sent. Without it, any bastion is accepted -- acceptable on a
private network, not across the internet.

A portal-only source has the same fields in its own form, under **SSH tunnel
(optional)**.

## The states a source can be in

| State | What it means | What to do |
|---|---|---|
| **Needs credentials** | Declared in the repository, but the portal has no credentials for it — or a required field is still blank. Reports that use it are held. | Press **Configure** and enter the credentials. |
| **Needs upload** | A file source declared with `upload: true` that has no file yet. Reports that use it are held. | Press **Upload** on the row and pick the file. |
| **Connected** | The portal can reach it. The detail says when it was last checked, or *file in repository* for a file committed to git. | Nothing. |
| **Failing** | The last connection check failed. The detail carries the error and how long it has been failing. Reports that use it are held. | Fix the credentials or the source itself, then **Test**. |
| **Not in repository** | A portal-only source: it exists here but your repository does not declare it. Not an error. | Nothing, unless you meant to declare it. |
| **Referenced but not declared** | A report's `data_sources` names something that neither the repository declares nor the portal has. Listed in its own section below the table. Reports that use it are held. | Declare it in `data-sources/config.yaml` and publish, or add a portal-only source with the same name. |

**Connected** and **Not in repository** are the only two states that let a
report build. Everything else holds it — see *What a held report shows* below.

A OneDrive/SharePoint source reads *not testable*: there is no connection
test for it yet, so run a report to verify it. A Google Sheets check opens the
spreadsheet and reads its first sheet with the stored credentials.

## File sources: committed, uploaded, or both

`path` says where the bytes live. `upload: true` says the portal is allowed to
replace them. They are independent, and all three combinations are useful:

- **Committed, no upload** — the file is in the repository. It reads as
  **Connected** (*file in repository*) as soon as it is published.
- **Uploaded** — the file is declared with `upload: true` but not committed.
  The source reads **Needs upload** until someone uploads it, then the portal
  owns the bytes. Nothing has to go through git to refresh it.
- **Both** — a file committed as a baseline that an analyst also refreshes by
  hand between pushes.

Each row gets **Upload** / **Replace file** and **Download** buttons, with a
progress bar for large files. Uploads are bounded by a per-file cap and your
organization's storage quota.

!!! warning
    Do not point an uploadable file at a path under `reports/`. Git sync
    rewrites that directory on every publish, so an upload there survives only
    until the next change to that report. Keep uploaded files somewhere else —
    `data-sources/files/…` is never touched by git. The form warns you if you
    try.

## Sharing credentials across studios

The same source name can be configured once for the whole organization.

On the Configure form, an org admin sees a checkbox:

> Store at organization level so every studio declaring `<name>` can use it

Ticking it saves the credentials under **Organization settings → Data
sources** instead of on this studio. Every studio in the organization that
declares a source with that name then connects with them, and the studio's row
is marked **organization**. Org admins can also add shared credentials there
directly; the *Used by* column tells them how many studios declare each name.

**A studio-level source shadows an organization-level one of the same name.**
That is the escape valve: one team can point at its own replica without
renaming anything in any report. Configure the source at studio level and it
takes precedence from the next build, with the shared credentials left intact
for everyone else.

Shared *files* work the same way — upload once at organization level and every
studio's reports read the same file by name.

## Portal-only sources

Sometimes there is no repository yet, or you need a source that has no business
being in the file. **Add a portal-only source (advanced)** at the bottom of the
Data sources page creates one directly: name, type, and the full set of fields
for that type, all editable here because nothing declares them elsewhere.

These are shown as **Not in repository**, detail *portal-only*. Reports
reference them by name exactly as they reference declared ones. It is a
deliberate escape hatch rather than the normal path — a source that lives only
in the portal is invisible to anyone reading the repository.

## What a held report shows

A report whose sources are not ready is **not started**. Nothing runs, nothing
fails halfway, and no partial output is published. Instead:

- On the dashboard, its card carries an amber **Needs setup** dot.
- Opening a report that has never built shows a page headed **`<slug>` is
  waiting for a data source**, naming the source and saying whether your
  repository declares it or the report references something undeclared.
- The **Operations** page counts held reports under *Waiting for data source*.

The moment a source's check passes — you configure it, upload its file, or
press **Test** and it succeeds — every report that was waiting on it is queued
automatically. There is nothing to re-run by hand.

A report that has already built keeps serving its last build even if its source
starts failing; the dashboard marks it **Source failing** so you know the page
is older than it looks.

## What a viewer sees

The held-report page is written for whoever is looking at it:

- A **studio admin** sees the source's name and a **Configure `<name>`** button
  straight to the form.
- A **developer** sees the source's name and a link to **Operations**.
- A **viewer** is told *Ask a studio admin to configure it* and nothing more.
  Connection errors can carry a host name or a user name, so they are never
  shown to someone who cannot act on them.

Uploading a file and testing a connection need the developer role; the Data
sources page itself, and configuring credentials, need studio admin.

## Next

- [Publishing from git](/docs/latest/portal/publishing-from-git/) — how a
  declaration reaches the portal
- [Organizations & studios](/docs/latest/portal/organizations-and-studios/) —
  where shared credentials live
- [Build failures](/docs/latest/troubleshooting/build-failures/)
