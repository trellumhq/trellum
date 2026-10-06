# Third-party software

Owned framework code is licensed under AGPL-3.0-only (see [LICENSE](LICENSE)).
That licence does not replace the terms for the
third-party software listed here, which keeps its own licence and its own
copyright holders.

Two different relationships appear below, and the distinction matters:

- **Bundled** — the file is in this repository and is redistributed with it.
  Everything under `static/vendor/`.
- **Depended upon** — installed by `pip` from the requirements files, never
  copied into this repository.

Bundled assets were last audited **2026-10-06** against
`static/vendor/MANIFEST.json` and the pinned upstream package notices. The
Python dependency tables were last audited **2026-08-16** against
`requirements.txt` and `requirements-drivers.txt`.
The missing DuckDB, pymssql, and boto3 entries were checked against the linked
upstream release notices on **2026-10-07**. This is a direct-dependency
inventory, not a complete inventory of every installed transitive dependency.

## Bundled — front-end libraries (`static/vendor/`)

Every one is redistributable with the framework under the licence shown below.
Their own copyright notices remain in the files as shipped, which is what each
of these licences asks for and the reason the minified files are not stripped.

| Library | Version | Licence |
| --- | --- | --- |
| Chart.js | 4.5.1 | MIT |
| chartjs-chart-funnel | 4.2.5 | MIT |
| chartjs-chart-geo | 4.3.3 | MIT |
| chartjs-chart-matrix | 2.0.1 | MIT |
| chartjs-chart-sankey | 0.12.1 | MIT |
| chartjs-chart-treemap | 2.3.1 | MIT |
| chartjs-chart-venn | 4.3.7 | MIT |
| chartjs-plugin-annotation | 3.1.0 | MIT |
| chartjs-plugin-datalabels | 2.2.0 | MIT |
| chartjs-plugin-zoom | 2.2.0 | MIT |
| Hammer.js | 2.0.8 | MIT |
| html2canvas | 1.4.1 | MIT |
| jsPDF | 2.5.2 | MIT |
| noUiSlider | 15.8.1 | MIT |
| Plotly.js | 2.27.0 | MIT |
| Slim Select | 2.9.2 | MIT |
| topojson-client | 3.1.0 | ISC |

## Bundled — fonts and data

| Asset | Source | Licence |
| --- | --- | --- |
| Inter (`static/vendor/fonts/inter-*.ttf`, `inter.css`) | Inter 4.001 (`66647c0bb`) | SIL Open Font License 1.1 |
| `static/vendor/countries-110m.json` | TopoJSON world-atlas, derived from Natural Earth | world-atlas: ISC; underlying Natural Earth data: public domain |

The SIL OFL permits redistribution of the font files, bundled with software,
with or without modification. It forbids selling the fonts on their own — which
we do not — and requires the reserved font name not be used for modified
versions, which we do not do either. The files ship unmodified.

## Depended upon — Python packages

Installed from PyPI rather than copied into this source tree. Their own
licences and packaged notices continue to apply, including when dependencies
are redistributed in a container image.

### `requirements.txt` — the framework core

| Package | Licence |
| --- | --- |
| pandas | BSD-3-Clause |
| numpy | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 |
| vertica-python | Apache-2.0 |
| openpyxl | MIT |
| python-dotenv | BSD-3-Clause |
| pyyaml | MIT |
| jinja2 | BSD-3-Clause |
| orjson | MPL-2.0 AND (Apache-2.0 OR MIT) |
| requests | Apache-2.0 |
| Pillow | MIT-CMU |

### `requirements-drivers.txt` — database and cloud sources

| Package | Licence |
| --- | --- |
| google-cloud-bigquery | Apache-2.0 |
| db-dtypes | Apache-2.0 |
| psycopg2-binary | LGPL-3.0-or-later, with the OpenSSL linking exception |
| pymysql | MIT |
| snowflake-connector-python | Apache-2.0 |
| clickhouse-connect | Apache-2.0 |
| duckdb | [MIT](https://github.com/duckdb/duckdb/blob/v1.0.0/LICENSE) |
| pymssql | [GNU LGPL 2.1](https://github.com/pymssql/pymssql/blob/v2.3.0/LICENSE) |
| trino | Apache-2.0 |
| databricks-sql-connector | Apache-2.0 |
| paramiko | LGPL-2.1-or-later |
| gspread | MIT |
| google-auth | Apache-2.0 |
| msal | MIT |
| pyarrow | Apache-2.0 |
| s3fs | BSD-3-Clause |
| gcsfs | BSD-3-Clause |
| fsspec | BSD-3-Clause |
| aiobotocore | Apache-2.0 |
| botocore | Apache-2.0 |
| boto3 | [Apache-2.0](https://github.com/boto/boto3/blob/1.36.3/LICENSE) |

The table includes LGPL packages and orjson's MPL licence expression. Refer to
each package's notices for its applicable terms rather than treating the
framework's own licence as a replacement for dependency licences.

The stricter case is **bundling**, not depending: anything added under
`static/vendor/` ships inside every copy of this repository. The current set
includes MIT JavaScript libraries, ISC packages, Inter fonts under SIL OFL 1.1,
and the Natural Earth public-domain data distributed through the ISC-licensed
world-atlas package. `static/vendor/MANIFEST.json` records the licence of each
entry; the package-specific upstream copyright notices, full licence texts,
and authoritative sources are in `static/vendor/LICENSES.md`.
