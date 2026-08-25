"""AWS cost estimation implementation."""

import json

from ...utils import get_runner_server_type


def check_prices(
    region: str, instance_types: list[str] = None, session=None
) -> dict[str, dict[str, float]]:
    """Fetch on-demand Linux prices from the AWS Pricing API.

    Returns a mapping of instance_type -> {region: hourly_usd_price}.
    The Pricing API endpoint is only available in us-east-1 regardless of
    which region the instances actually run in.

    session: optional boto3.Session to use (uses provider credentials);
             falls back to the default session when None.
    """
    import boto3

    if session is None:
        session = boto3.Session()
    client = session.client("pricing", region_name="us-east-1")

    filters = [
        {"Type": "TERM_MATCH", "Field": "operatingSystem", "Value": "Linux"},
        {"Type": "TERM_MATCH", "Field": "tenancy", "Value": "Shared"},
        {"Type": "TERM_MATCH", "Field": "capacitystatus", "Value": "Used"},
        {"Type": "TERM_MATCH", "Field": "preInstalledSw", "Value": "NA"},
        {"Type": "TERM_MATCH", "Field": "regionCode", "Value": region},
    ]

    prices = {}
    paginator = client.get_paginator("get_products")

    for page in paginator.paginate(ServiceCode="AmazonEC2", Filters=filters):
        for price_item_str in page["PriceList"]:
            data = json.loads(price_item_str)
            attrs = data.get("product", {}).get("attributes", {})
            instance_type = attrs.get("instanceType")

            if not instance_type:
                continue
            if instance_types and instance_type not in instance_types:
                continue

            on_demand = data.get("terms", {}).get("OnDemand", {})
            for term in on_demand.values():
                for pd in term.get("priceDimensions", {}).values():
                    price_usd = float(pd.get("pricePerUnit", {}).get("USD", 0) or 0)
                    if price_usd > 0:
                        prices.setdefault(instance_type, {})[region] = price_usd

    return prices


def get_server_price(
    server_prices: dict[str, dict[str, float]],
    server_type: str,
    server_location: str,
    ipv4_price: float = 0.0,
    ipv6_price: float = 0.0,
) -> float:
    """Get hourly on-demand price for an EC2 instance type in USD.

    ipv4_price and ipv6_price are accepted for interface compatibility but
    ignored — AWS billing for Elastic IPs is separate and outside scope.

    server_location may be an AZ (e.g. 'us-east-1a'), a region ('us-east-1'), or
    None; check_prices fetches exactly one region, so an exact match wins and
    anything else (an AZ, or None) falls back to that single entry.
    """
    try:
        region_prices = server_prices[server_type]
    except (KeyError, TypeError):
        return None
    if server_location is not None and server_location in region_prices:
        return region_prices[server_location]
    return next(iter(region_prices.values()), None)


def get_runner_server_price_per_second(
    server_prices: dict[str, dict[str, float]],
    runner_name: str,
    ipv4_price: float = 0.0,
    ipv6_price: float = 0.0,
) -> tuple[float, str]:
    """Get runner server price per second for AWS."""

    price_per_second = None

    server_type = get_runner_server_type(runner_name)
    server_price_per_hour = get_server_price(server_prices, server_type, None)

    if server_price_per_hour is not None:
        price_per_second = server_price_per_hour / 3600

    return price_per_second, server_type
