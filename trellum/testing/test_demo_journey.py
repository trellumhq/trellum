"""fact_journey and the two v2 custom sections, held to their contracts.

The journey table drives monetization's flow map, and the map hard-codes the
node names it lays out -- so the fixture's graph and the section's graph must
never drift apart. The generation model is conservation-based (what enters a
node leaves it), and its baked stories are what the demo narrates: the big
tiers buy after losses, non-spenders never buy, retargeting is how whales
come back. These tests pin all of that, plus the wiring contracts of the
rewritten Insert Coin game (Start-gated, filter-live).
"""

import importlib.util
import os

import numpy as np
import pandas as pd
import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_DEMO = os.path.join(_HERE, "..", "demo")

_NODES = {"paid", "organic", "retarget", "easy", "mid", "hard",
          "win", "lose", "checkout", "purchase", "exit"}


def _load(path: str, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def mf():
    return _load(os.path.join(_DEMO, "tools", "make_fixtures.py"), "mf_journey")


@pytest.fixture(scope="module")
def journey(mf):
    from datetime import date
    dates = pd.date_range(end=pd.Timestamp(date(2026, 6, 30)), periods=45,
                          freq="D")
    rng = np.random.default_rng(7)
    return mf.build_fact_journey(dates, date(2026, 6, 30), rng)


def test_journey_nodes_are_the_declared_graph(journey):
    seen = set(journey["from_node"]) | set(journey["to_node"])
    assert seen <= _NODES, f"unexpected nodes: {seen - _NODES}"
    # every node participates; a silent hole in the graph would render as a
    # dead station on the map
    assert seen == _NODES


def test_non_spenders_reach_checkout_but_never_buy(journey):
    ns = journey[journey["spender_tier"] == "non_spender"]
    reached = ns[(ns["to_node"] == "checkout")]["transitions"].sum()
    bought = ns[(ns["from_node"] == "checkout") &
                (ns["to_node"] == "purchase")]["transitions"].sum()
    assert reached > 0, "non-spenders should still visit checkout"
    assert bought == 0, "non-spenders must never purchase"


def test_big_tiers_buy_after_losses(journey):
    for tier in ("whale", "dolphin"):
        sub = journey[journey["spender_tier"] == tier]
        via_loss = sub[(sub["from_node"] == "lose") &
                       (sub["to_node"] == "checkout")]["transitions"].sum()
        via_win = sub[(sub["from_node"] == "win") &
                      (sub["to_node"] == "checkout")]["transitions"].sum()
        assert via_loss > via_win * 2, (
            f"{tier}: loss-driven checkout should dominate "
            f"(loss={via_loss}, win={via_win})")


def test_retargeting_skews_to_whales(journey):
    def retarget_share(tier):
        sub = journey[(journey["spender_tier"] == tier) &
                      (journey["from_node"].isin(["paid", "organic",
                                                  "retarget"]))]
        total = sub["transitions"].sum()
        ret = sub[sub["from_node"] == "retarget"]["transitions"].sum()
        return ret / total if total else 0
    assert retarget_share("whale") > retarget_share("non_spender") * 2


def test_flow_is_conservation_consistent(journey):
    # What enters win/lose/checkout leaves it, within Poisson noise. The
    # tail the lap loop drops is on the LEVEL nodes, not these.
    for node in ("win", "lose", "checkout"):
        into = journey[journey["to_node"] == node]["transitions"].sum()
        out = journey[journey["from_node"] == node]["transitions"].sum()
        assert abs(into - out) / max(into, 1) < 0.05, (
            f"{node}: in={into}, out={out}")


# ── Drift guards: fixture graph <-> section JS, and the game's wiring ────

def test_journey_section_lays_out_every_fixture_node():
    cs = _load(os.path.join(_DEMO, "reports", "monetization",
                            "custom_sections.py"), "mon_cs")
    for node in _NODES:
        assert node + ":" in cs.JOURNEY_JS, (
            f"journey map lost the '{node}' station")
    assert "_journey" in cs.JOURNEY_JS
    assert "fw.filterEngine.subscribe" in cs.JOURNEY_JS


def test_game_is_start_gated_and_filter_live():
    cs = _load(os.path.join(_DEMO, "reports", "insert-coin",
                            "custom_sections.py"), "ic_cs")
    # the owner's rule: nothing moves until Start
    assert "rmStart" in cs.CABINET_JS, "the Start button wiring is gone"
    assert "'ready'" in cs.CABINET_JS, "the at-rest chart state is gone"
    # the wiring the validator can't literally see
    assert "_arcade" in cs.CABINET_JS
    assert "fw.filterEngine.subscribe" in cs.CABINET_JS
    for key in ("payload.tiers", "payload.pit_fraction"):
        assert key in cs.CABINET_JS, f"JS no longer reads {key}"
    gen_path = os.path.join(_DEMO, "reports", "insert-coin", "generator.py")
    with open(gen_path, encoding="utf-8") as fh:
        gen = fh.read()
    for key in ('"tiers"', '"pit_fraction"'):
        assert key in gen, f"generator no longer ships {key}"


def test_the_demo_pins_the_trellum_dark_theme():
    """The demo is the first thing anyone sees of Trellum, so it declares the
    brand theme rather than inheriting whatever the default happens to be.

    `theme:` in the project-root config is rung 0 of the portal's resolution
    chain -- it outranks the studio default and every viewer's own override
    -- so declaring it here is what makes a freshly seeded demo look like
    Trellum for everyone, not only for whoever has not changed their theme.

    This is asserted because the failure is silent and cosmetic: the file was
    empty for a while, a hand-edited running instance had the pin, and a
    fresh `scripts/demo.sh` would have quietly produced an unbranded demo.
    """
    import yaml

    with open(os.path.join(_DEMO, "config.yaml"), encoding="utf-8") as fh:
        config = yaml.safe_load(fh) or {}

    from trellum.themes import THEME_REGISTRY

    assert config.get("theme") == "trellum dark"
    assert config["theme"] in THEME_REGISTRY
