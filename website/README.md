# Trellum website

The Trellum website is a static landing page, documentation, and blog site.
Its owned source is AGPL-3.0-only and the generated artifact can be served by GitHub
Pages or any ordinary web server.

The product documentation is supplied from this checkout before export:

```sh
./scripts/sync-docs.sh
cd website
python -m pip install -r requirements-test.txt -c constraints.txt
python manage.py export_static --output dist --base-url https://trellum.dev \
  --demo-artifact ../trellum/demo/output
```

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
