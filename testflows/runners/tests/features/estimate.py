"""Tests for the cost-estimate pricing dispatch (testflows/runners/estimate.py).

The estimate CLI works on historical runner names, which carry no provider, so
it dispatches pricing by which provider's price map contains the server type
(not string-sniffing). server_prices is keyed per-provider, matching metrics.py
and the runtime.
"""
from testflows.core import *

from testflows.runners import estimate


def _prices():
    """Per-provider price map: {name: {"prices": {type: {loc: price}}, "currency": ...}}."""
    return {
        "hetzner": {"prices": {"cx23": {"nbg1": 5.0}}, "currency": "EUR"},
        "aws": {"prices": {"t3.medium": {"us-east-1": 4.0}}, "currency": "USD"},
        # Scaleway type contains a dot — the old "." heuristic mis-routed it to AWS.
        "scaleway": {"prices": {"basic2.a16c.32g": {"fr-par-1": 3.0}}, "currency": "EUR"},
    }


@TestScenario
def get_server_price_dispatches_by_membership(self):
    """Each type is priced by the provider whose map contains it, not by a heuristic."""
    prices = _prices()
    with Then("a dotted Scaleway type is priced by Scaleway, not AWS"):
        assert estimate.get_server_price(prices, "basic2.a16c.32g", "fr-par-1") == 3.0
    with And("a Hetzner type is priced by Hetzner (with IP costs added)"):
        assert estimate.get_server_price(prices, "cx23", "nbg1", 1.0, 0.5) == 6.5
    with And("an AWS type is priced by AWS"):
        assert estimate.get_server_price(prices, "t3.medium", "us-east-1") == 4.0
    with And("an unknown type returns None"):
        assert estimate.get_server_price(prices, "nope.type", "x") is None


@TestScenario
def runner_price_per_second_routes_scaleway(self):
    """A Scaleway runner name is priced via Scaleway (proving it isn't sent to AWS)."""
    prices = _prices()
    with When("pricing a Scaleway runner"):
        price, server_type = estimate.get_runner_server_price_per_second(
            prices, "github-runner-30-9-basic2.a16c.32g"
        )
    with Then("the type decodes and the per-second price comes from the Scaleway map"):
        assert server_type == "basic2.a16c.32g", server_type
        assert price == 3.0 / 3600, price


@TestFeature
@Name("estimate")
def feature(self):
    """Cost-estimate pricing dispatch."""
    for scenario in loads(current_module(), Scenario):
        scenario()
