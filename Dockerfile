# trellum — two images from one source tree.
#
#   portal  (default target) the Django control plane: web / worker / migrate,
#           dispatched by docker/entrypoint.sh
#   runner  the sandbox image: JUST the framework + its deps, no Django,
#           no portal source, no git. The worker starts one throwaway `runner`
#           container per report build so tenant Python never shares the
#           worker's uid, PID namespace, secrets, or /data view.
#
# Build from this repository's root:
#   docker build -t trellum .                              # portal (last stage)
#   docker build --target runner -t trellum-runner .       # runner
#
# All durable state lives in Postgres and the /data volume; containers are
# disposable.

# ── runner: the report-build sandbox image ─────────────────────────────────
FROM python:3.12-slim AS runner

WORKDIR /app

ARG PIP_INDEX_URL=https://pypi.org/simple
ARG SOURCE_URL=

# Apply Debian security updates that landed after the base image was built.
RUN apt-get update \
    && apt-get upgrade -y \
    && rm -rf /var/lib/apt/lists/*

# Only the framework's own dependencies. constraints.txt still pins the exact
# resolved versions we tested and shipped an SBOM for. Django, gunicorn, the
# AI SDKs and git are deliberately absent — this image runs untrusted code.
COPY trellum/requirements.txt trellum/requirements-drivers.txt ./trellum/
COPY constraints.txt ./
RUN pip install --no-cache-dir --index-url "${PIP_INDEX_URL}" \
    -c constraints.txt \
    -r trellum/requirements.txt \
    -r trellum/requirements-drivers.txt

# Third-party licence texts, generated from what is actually installed in THIS
# layer, so the disclosure cannot drift from the image. pip-licenses is removed
# again in the same layer: it is a build tool, not part of the product.
RUN pip install --no-cache-dir --index-url "${PIP_INDEX_URL}" pip-licenses \
    && pip-licenses --with-license-file --no-license-path --format=plain-vertical \
        --output-file THIRD-PARTY-LICENSES.txt \
        --ignore-packages pip-licenses prettytable wcwidth \
    && pip uninstall -y pip-licenses prettytable wcwidth

# The framework at /app/trellum; cwd=/app makes this source tree win sys.path,
# matching how the worker invokes `python -m trellum.run`.
COPY trellum/ trellum/

# The product terms and third-party notices travel with the artifact.
COPY LICENSE NOTICE ./

# The sandbox normally passes the same explicit uid. Keep the image safe when
# it is run directly too; everything under /app is world-readable.
RUN groupadd --gid 10001 trellum \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin trellum
ENV TRELLUM_SOURCE_URL=$SOURCE_URL \
    PYTHONUNBUFFERED=1
USER 10001:10001


# ── portal: the Django control plane ───────────────────────────────────────
# The same image runs every role; the entrypoint picks by CMD:
#   web      gunicorn serving the portal (default)
#   worker   the report runner / scheduler process
#   migrate  one-shot database migration (compose runs it before web/worker)
FROM python:3.12-slim AS portal

WORKDIR /app

# git: per-studio reports-repo sync. curl: compose healthchecks.
RUN apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends git curl \
    && rm -rf /var/lib/apt/lists/*

ARG PIP_INDEX_URL=https://pypi.org/simple

# Dependency layer first, so source edits don't re-run pip.
# constraints.txt locks the FULL transitive tree: a shipped build must
# resolve to exactly what we tested and published an SBOM for.
# requirements-test.txt is deliberately NOT installed — a test runner in a
# shipped image is pure CVE surface.
COPY trellum/requirements.txt trellum/requirements-drivers.txt ./trellum/
COPY requirements.txt requirements-django.txt constraints.txt ./
RUN pip install --no-cache-dir --index-url "${PIP_INDEX_URL}" \
    -c constraints.txt \
    -r requirements-django.txt \
    -r trellum/requirements-drivers.txt

# Third-party licence texts, generated from what is actually installed in THIS
# layer, so the disclosure cannot drift from the image. pip-licenses is removed
# again in the same layer: it is a build tool, not part of the product.
RUN pip install --no-cache-dir --index-url "${PIP_INDEX_URL}" pip-licenses \
    && pip-licenses --with-license-file --no-license-path --format=plain-vertical \
        --output-file THIRD-PARTY-LICENSES.txt \
        --ignore-packages pip-licenses prettytable wcwidth \
    && pip uninstall -y pip-licenses prettytable wcwidth

# Side-by-side packages at /app: `import trellum` and `import apps` both
# resolve from the working directory. The worker spawns report builds with
# cwd=/app so the bundled framework always wins sys.path over any trellum/
# that happens to sit inside a studio's project tree.
COPY trellum/ trellum/
COPY trellum_portal/ trellum_portal/
COPY apps/ apps/
COPY templates/ templates/
COPY static/ static/
COPY docker/ docker/
COPY manage.py .
# The terms travel with the artifact.
COPY LICENSE NOTICE ./

# collectstatic MUST run with manifest storage (settings.build inherits the
# production storage config) — runtime prod settings look static files up in
# the manifest, and a missing manifest 500s every {% static %} page.
RUN chmod +x docker/entrypoint.sh docker/git-askpass.sh \
    && DJANGO_SETTINGS_MODULE=trellum_portal.settings.build python manage.py collectstatic --noinput

# One volume holds all studio state: git checkouts, materialized project
# roots, report output, run sandboxes/logs, backups.
ENV TRELLUM_DATA_DIR=/data
RUN mkdir -p /data
VOLUME ["/data"]

# Run as an unprivileged user. This is a basic security review item, and it
# matters more here than in most apps: report builds
# are tenant-authored Python, and they inherit this uid.
#
# UPGRADING AN EXISTING INSTALL: /data on an existing volume is owned by root
# and this user cannot write it. Run once, with the stack stopped:
#   docker compose run --rm --user root web chown -R 10001:10001 /data
# The `doctor` volume check reports the failure clearly if you forget.
RUN groupadd --gid 10001 trellum \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin trellum \
    && chown -R trellum:trellum /app /data
USER trellum

ARG GIT_SHA=unknown
ARG BUILD_TIME=unknown
ARG GIT_BRANCH=unknown
ARG SOURCE_URL=
ENV TRELLUM_VERSION_SHA=$GIT_SHA \
    TRELLUM_VERSION_BUILD_TIME=$BUILD_TIME \
    TRELLUM_VERSION_BRANCH=$GIT_BRANCH \
    TRELLUM_SOURCE_URL=$SOURCE_URL \
    DJANGO_SETTINGS_MODULE=trellum_portal.settings.prod \
    PYTHONUNBUFFERED=1

EXPOSE 8050

ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["web"]
