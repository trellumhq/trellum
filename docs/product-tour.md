# Trellum portal feature tour

[Explore the interactive tour](https://trellum.dev/tour/) at your own pace.
Use Previous and Next, or choose a feature directly. Each slide includes a
real screenshot, an explanation of what to look for, and relevant documentation.
Nothing advances automatically; open any screenshot at full size for detail.

The illustrative user/agent dialogue describes ordinary repository work with
Python, SQL and YAML. Screenshots show the actual portal and reports; they are
not a recording of a coding agent performing the work. The portal's optional
Buddy assistant is separate from the coding agent used to author reports.

## Walkthrough

| Step | Feature | User and coding agent |
|---|---|---|
| 1 | A shared home for reports | **User:** Can my team explore the data themselves? **Agent:** Build reports in a repository, then use the optional portal as your team's shared home. |
| 2 | Connect your sources | **User:** Use our database and acquisition budget. **Agent:** Declare source names in the project. An admin supplies credentials. This demo uses synthetic SQLite and CSV data. |
| 3 | Build with your coding agent | **User:** Create a revenue and retention report. **Agent:** Use Codex, Claude Code, or your preferred agent to edit Python, SQL and YAML, build locally, and validate the output. |
| 4 | Define metrics once | **User:** Keep revenue consistent across reports. **Agent:** Define it in metrics.yaml. Reports claim metric IDs; the catalog shows their definitions, versions and usage. |
| 5 | Review and publish with Git | **User:** Show me the changes before publishing. **Agent:** Review the diff and commit history, then publish the reviewed revision. This screen connects your repository; the demo has no remote configured. |
| 6 | Put events beside the numbers | **User:** Mark releases and campaigns. **Agent:** Add events.yaml declarations in Git. The portal calendar and report charts show their context. |
| 7 | Follow the whole experiment portfolio | **User:** Which tests are running, and what changed? **Agent:** The overview reads declared tests and their built results, including confidence intervals and lifecycle status. |
| 8 | Schedule and inspect builds | **User:** Keep reports fresh and show failures. **Agent:** Operations shows schedules, build status, validation and logs. These demo builds completed successfully. |
| 9 | Control access and sharing | **User:** Can we share a report safely? **Agent:** Use organization and studio roles, plus configurable share links, passwords and expiry. Sharing is disabled in this demo. |
| 10 | See which reports get used | **User:** What does the team actually read? **Agent:** Report Analytics tracks views and unique viewers so you can review report usage. |
| 11 | Query at view time when needed | **User:** Can some reports show current detail? **Agent:** Authors can declare live queries. They need a backend; the organization controls query limits. |
| 12 | Describe what should trigger an alert | **User:** Watch for a drop in retention. **Agent:** Write a report-specific rule in plain language. This unsaved example needs an AI provider and delivery setup before it can run. |
| 13 | Ask questions inside the portal | **User:** Can readers ask a follow-up question? **Agent:** Optional Buddy uses an administrator-configured model provider. It is separate from your coding agent and is not enabled here. |
| 14 | Reports that people can explore | **User:** What else can a report show? **Agent:** Store Health is a separate retail example: comparison KPIs, filters, chart annotations, revenue and margin in Trellum Dark. |

## What the demo shows

- The public [demo gallery](https://trellum.dev/demo/) serves interactive
  reports only. It is not a hosted portal. The portal is a self-hosted backend;
  see [Try Trellum locally](https://trellum.dev/docs/latest/install/try-it/).
- The demo repository includes a separate worked analysis, **Where Northwind
  loses buyers**, with two fixed synthetic September 2026 captures. Capture and
  build timestamps describe artifact creation, not the reporting period. The
  analysis format and local workflow are covered in
  [Publish an analysis](https://trellum.dev/docs/latest/workflow/published-analyses/).
- The portal screens use the Nova Play organization and Product Insights
  studio. Demo sources are synthetic local SQLite (`demo_db`) and CSV
  (`ua_budget`); there is no live warehouse connection.
- The demo has no remote Git repository, Buddy provider or email configuration.
  The repository screen is setup, not a completed remote sync. The alert is
  unsaved, and AI-backed alert evaluation and delivery are not demonstrated.
- Portal operations show successful seeded builds. The portal reads built
  experiment results and declared event annotations; this tour does not claim
  that an agent created a diff, changed a report, or synchronized Git during capture.
- The local coding agent and portal Buddy are separate. Buddy is optional and
  requires an administrator to configure a model provider.
- Store Health is a separate synthetic retail report, not part of Nova Play's
  game data.
