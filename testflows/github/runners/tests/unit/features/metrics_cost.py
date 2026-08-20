"""Cost metrics: per-provider price resolution in metrics.update_servers.

Regression coverage for the provider price-function dispatch — notably that
Scaleway servers get a cost estimate (they were silently skipped because
``scaleway`` was missing from the provider price-fn map, so the dashboard showed
no Scaleway cost).
"""
from types import SimpleNamespace

from testflows.core import *

from testflows.github.runners import metrics


def _server(provider_name, server_type, location, status="running"):
    """A minimal RunnerServer-shaped object for update_servers."""
    return SimpleNamespace(
        server_status=status,
        status=status,
        provider_name=provider_name,
        server_type=server_type,
        server_location=location,
        name=f"github-runner-{provider_name}-1",
        server=SimpleNamespace(id=f"{provider_name}-1", created=None,
                               public_net=None, image=None),
    )


def _cost(server_type, location, currency="EUR"):
    return metrics.COST_ESTIMATE.labels(
        server_type=server_type, location=location, currency=currency
    )._value.get()


@TestScenario
def scaleway_cost_recorded_by_zone(self):
    """A Scaleway server is priced by its zone (regression: previously skipped)."""
    metrics.COST_ESTIMATE.clear()
    prices = {
        "scaleway": {
            "prices": {"basic2.a16c.32g": {"fr-par-1": 0.5}},
            "currency": "EUR",
        }
    }
    with When("update_servers runs for a Scaleway server"):
        metrics.update_servers(
            [_server("scaleway", "basic2.a16c.32g", "fr-par-1")],
            server_prices=prices,
        )
    with Then("the cost estimate is set from the zone-keyed price"):
        assert _cost("basic2.a16c.32g", "fr-par-1") == 0.5, _cost(
            "basic2.a16c.32g", "fr-par-1"
        )


@TestScenario
def hetzner_and_aws_cost_still_recorded(self):
    """The Hetzner and AWS branches still price correctly (no regression)."""
    metrics.COST_ESTIMATE.clear()
    prices = {
        "hetzner": {"prices": {"cx22": {"nbg1": 1.0}}, "currency": "EUR"},
        "aws": {"prices": {"t3.medium": {"us-east-1": 2.0}}, "currency": "USD"},
    }
    with When("update_servers runs for Hetzner + AWS servers"):
        metrics.update_servers(
            [
                _server("hetzner", "cx22", "nbg1"),
                _server("aws", "t3.medium", "us-east-1a"),  # AZ -> region
            ],
            server_prices=prices,
        )
    with Then("Hetzner is priced by location"):
        assert _cost("cx22", "nbg1", "EUR") == 1.0, _cost("cx22", "nbg1", "EUR")
    with And("AWS is priced by region translated from the AZ"):
        assert _cost("t3.medium", "us-east-1a", "USD") == 2.0, _cost(
            "t3.medium", "us-east-1a", "USD"
        )


@TestFeature
@Name("metrics cost")
def feature(self):
    for scenario in loads(current_module(), Scenario):
        scenario()
