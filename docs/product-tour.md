# Trellum portal feature tour

This three-minute video is a **silent, captioned feature tour** made from
screenshots of the actual Trellum portal and reports. The user/agent dialogue
is illustrative. It is not voiceover, a live screen recording, or evidence of
a coding agent performing the depicted work. The coding workflow described is
ordinary repository work with Python, SQL and YAML; the portal's optional
Buddy assistant is a separate feature.

## Transcript

| Time | Scene | Dialogue |
|---|---|---|
| 0:00–0:10 | A shared home for reports | **User:** Can my team explore the data themselves? **Agent:** Build reports in a repository, then use the optional portal as your team's shared home. |
| 0:10–0:24 | Connect your sources | **User:** Use our database and acquisition budget. **Agent:** Declare source names in the project. An admin supplies credentials. This demo uses synthetic SQLite and CSV data. |
| 0:24–0:40 | Build with your coding agent | **User:** Create a revenue and retention report. **Agent:** Use Codex, Claude Code, or your preferred agent to edit Python, SQL and YAML, build locally, and validate the output. |
| 0:40–0:52 | Define metrics once | **User:** Keep revenue consistent across reports. **Agent:** Define it in metrics.yaml. Reports claim metric IDs; the catalog shows their definitions, versions and usage. |
| 0:52–1:06 | Review and publish with Git | **User:** Show me the changes before publishing. **Agent:** Review the diff and commit history, then publish the reviewed revision. This screen connects your repository; the demo has no remote configured. |
| 1:06–1:18 | Put events beside the numbers | **User:** Mark releases and campaigns. **Agent:** Add events.yaml declarations in Git. The portal calendar and report charts show their context. |
| 1:18–1:30 | Follow the whole experiment portfolio | **User:** Which tests are running, and what changed? **Agent:** The overview reads declared tests and their built results, including confidence intervals and lifecycle status. |
| 1:30–1:42 | Schedule and inspect builds | **User:** Keep reports fresh and show failures. **Agent:** Operations shows schedules, build status, validation and logs. These demo builds completed successfully. |
| 1:42–1:54 | Control access and sharing | **User:** Can we share a report safely? **Agent:** Use organization and studio roles, plus configurable share links, passwords and expiry. Sharing is disabled in this demo. |
| 1:54–2:04 | See which reports get used | **User:** What does the team actually read? **Agent:** Report Analytics tracks views and unique viewers so you can review report usage. |
| 2:04–2:14 | Query at view time when needed | **User:** Can some reports show current detail? **Agent:** Authors can declare live queries. They need a backend; the organization controls query limits. |
| 2:14–2:28 | Describe what should trigger an alert | **User:** Watch for a drop in retention. **Agent:** Write a report-specific rule in plain language. This unsaved example needs an AI provider and delivery setup before it can run. |
| 2:28–2:42 | Ask questions inside the portal | **User:** Can readers ask a follow-up question? **Agent:** Optional Buddy uses an administrator-configured model provider. It is separate from your coding agent and is not enabled here. |
| 2:42–2:54 | Reports that people can explore | **User:** What else can a report show? **Agent:** Store Health is a separate retail example: comparison KPIs, filters, chart annotations, revenue and margin in Trellum Dark. |
| 2:54–3:00 | Try Trellum | **User:** Where do I start? **Agent:** pip install trellum. Browse report demos at trellum.dev/demo, or run the portal locally. |

## What the demo shows

- The public [demo gallery](https://trellum.dev/demo/) serves interactive
  reports only. It is not a hosted portal. The portal is a self-hosted backend;
  see [Try Trellum locally](https://trellum.dev/docs/latest/install/try-it/).
- The portal screens use the Nova Play organization and Product Insights
  studio. Demo sources are synthetic local SQLite (`demo_db`) and CSV
  (`ua_budget`); there is no live warehouse connection.
- The demo has no remote Git repository, Buddy provider or email configuration.
  The repository screen is setup, not a completed remote sync. The alert is
  unsaved, and AI-backed alert evaluation and delivery are not demonstrated.
- Portal operations show successful seeded builds. The portal reads built
  experiment results and declared event annotations; this tour does not claim
  that the video agent created a diff, changed a report, or synchronized Git.
- The local coding agent and portal Buddy are separate. Buddy is optional and
  requires an administrator to configure a model provider.
- Store Health is a separate synthetic retail report, not part of Nova Play's
  game data.
