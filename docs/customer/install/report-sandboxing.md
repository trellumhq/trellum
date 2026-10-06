# Report sandboxing

Report code (`reports/<slug>/generator.py`) is authored outside the portal's
trust boundary: anyone who can push to a studio's repository, or who is a
studio admin, supplies it. Each build runs in its own disposable container so
that code is never trusted more than that boundary allows.

## What each sandbox gets

- Non-root, read-only root filesystem, every Linux capability dropped,
  `no-new-privileges`.
- Only that build's own directories: its working copy read-write, the studio
  project read-only, its output directory read-write, and its own
  organization's shared data-source files read-only when a report uses one.
  It never sees another studio or organization, the git checkout (which holds
  the repository token), or the worker's own environment.
- Memory, CPU and process-count limits — see
  [Sizing](/docs/latest/operations/sizing/) for operator-controlled build budgets.
- A dedicated network that **cannot reach the rest of the deployment** — so
  even a leaked warehouse credential cannot be turned against your database or
  the portal itself. Network egress is denied by default. Reports that need a
  remote warehouse or API require operator-approved outbound access.
  `TRELLUM_SANDBOX_EGRESS=open` enables broad internet access; it does not
  provide a per-destination allowlist. Use your host or network firewall to
  restrict access to approved destinations. See the
  [configuration reference](/docs/latest/install/configuration/#security).

The worker is the only process holding the Docker socket; report code never
touches it.

## Host firewall rule

Docker's own network isolation already fences sandboxes off from the rest of
the deployment. Two things only a host rule can block, so a one-time script
adds them:

- the cloud **instance metadata endpoint** (`169.254.169.254`) — on a cloud VM
  this hands out the instance's own credentials, so blocking it is
  **mandatory**
- services bound to the host itself

```bash
sudo docker/harden-sandbox-net.sh
```

Run it once after the stack is up, then persist it with your firewall tooling
(`iptables-persistent`, firewalld, or your cloud provider's rules). Private-LAN
ranges stay reachable by default — a self-hosted warehouse is often on the
LAN — with a stricter, commented-out variant in the script for locking those
down too.

## What this does not protect against

Stated plainly, so you can judge it against your own threat model:

- **The worker holds the Docker socket**, which is root-equivalent on the
  host. It runs only first-party code and never hands the socket to a
  sandbox, but a bug in the worker itself would be high-value. A socket proxy
  or rootless Docker would reduce this further; neither is in place yet.
- **Sandboxes share the host kernel** — they are containers, not VMs. A
  kernel container-escape is not mitigated. If your threat model needs it,
  point Docker at a stricter runtime such as gVisor (`runsc`); the sandbox
  settings are runtime-agnostic.
- **Outbound access requires operator approval.** The default sandbox network
  blocks egress. Setting `TRELLUM_SANDBOX_EGRESS=open` lets tenant report code
  reach the internet broadly while holding its studio's warehouse credentials;
  use host or network firewall rules to constrain approved destinations. The
  setting is instance-wide, not per studio.
- **A build can read its own studio's project**, including other reports'
  output in the same studio. The trust boundary is the studio, not the
  individual report.

## Turning it off (development only)

Setting `TRELLUM_SANDBOX=off` runs builds as unsandboxed subprocesses on the
worker host. It exists for local development without a sandbox Docker setup.
**Never set it on a real deployment**: it runs tenant code with the worker's
host access, and the worker logs a
warning on every boot while it is off outside local development.
