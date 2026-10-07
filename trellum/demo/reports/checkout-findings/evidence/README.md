# Evidence for the Northwind worked analysis

These are real PNG captures from the synthetic `cart-funnel` demo report,
imported with Trellum's analysis capture importer. Each matching JSON file
preserves the active filters, component reference, and actual capture/build
timestamps. The local preview URL was replaced with the canonical public demo
reference, `https://trellum.dev/demo/cart-funnel/`; no chart pixels, counts,
filters, or timestamps were edited.

The reporting period is 2026-09-01 through 2026-09-30, all channels, with
Mobile and Desktop captured separately. The source fixture used 420 days,
seed 42, and anchor 2026-10-07. Source table: `shop_traffic`. The report's
theme picker was set to **trellum light** for these captures.

To inspect the same source snapshot, generate the full demo with
`python -m trellum.demo --full --anchor 2026-10-07`, set the environment
variable `FW_NOW=2026-10-07`, and build `cart-funnel`
with `python -m trellum.run reports/cart-funnel --no-serve`,
then set its **Session to Purchase** filters to the period and device above.
The framework may evolve, so reproducing the exact pixels also requires the
corresponding Git revision. Rebuilding this article requires neither the
source report nor the warehouse: its committed evidence is sufficient.
