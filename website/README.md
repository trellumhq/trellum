# Trellum website

The Trellum website is a static landing page, documentation, and blog site.
Its owned source is AGPL-3.0-only and the generated artifact can be served by GitHub
Pages or any ordinary web server.

The product documentation is supplied from this checkout's stable release tags
before export. Edit the canonical sources in `docs/customer/`; generated files
under `website/content/docs/` are ignored. `latest` is a copy of the newest
stable tag's documentation, not the working tree. Documentation corrections
land in that published corpus with the next release; changing the website
alone does not update existing release snapshots.

```sh
./scripts/sync-docs.sh
cd website
python -m pip install -r requirements-test.txt -c constraints.txt
python manage.py export_static --output dist --base-url https://trellum.dev \
  --demo-artifact ../trellum/demo/output
```

The demo artifact must already contain the portable gallery. Build it with the
steps in `.github/workflows/pages.yml`, or use
`--demo-artifact tests/fixtures/demo-gallery` for a website-only preview. The
fixture is a test page, not the public report demos. The test suite also renders
the current `docs/customer/` sources so edits are checked before release.

Serve `website/dist` at the domain root. The exporter imports a portable demo
gallery at `/demo/`, writes trailing-slash
routes as `index.html`, keeps RSS, JSON and text filenames, copies the site
assets, and adds `.nojekyll`. It fails when the documentation corpus, nav
entries, figures or rendered placeholders are incomplete.

```sh
cd website
pytest
python manage.py check
python manage.py export_static --output dist --base-url https://trellum.dev \
  --demo-artifact ../trellum/demo/output
```

The site uses Django templates only at build time, hand-written CSS, vanilla
JavaScript and the existing Markdown content format. No signup, purchase or
runtime service is required.
