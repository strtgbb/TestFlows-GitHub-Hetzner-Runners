"""Hetzner Cloud cost estimation implementation."""

from ...utils import get_runner_server_type


def get_server_price(
    server_prices: dict[str, dict[str, float]],
    server_type: str,
    server_location: str,
    ipv4_price: float,
    ipv6_price: float,
) -> float:
    """Get server price for Hetzner Cloud.

    server_location may be None (the runner path passes it), so fall back to the
    single fetched location -- check_prices fetches exactly one -- rather than
    looking up server_prices[type][None] and getting nothing.
    """
    ipv4_price = ipv4_price or 0
    ipv6_price = ipv6_price or 0
    try:
        location_prices = server_prices[server_type]
    except (KeyError, TypeError):
        return None
    if server_location is not None and server_location in location_prices:
        base = location_prices[server_location]
    else:
        base = next(iter(location_prices.values()), None)
    return None if base is None else base + ipv4_price + ipv6_price


def get_runner_server_price_per_second(
    server_prices: dict[str, dict[str, float]],
    runner_name: str,
    ipv4_price: float,
    ipv6_price: float,
) -> tuple[float, str]:
    """Get runner server price per second for Hetzner Cloud."""

    price_per_second = None

    server_type = get_runner_server_type(runner_name)
    server_price_per_hour = get_server_price(
        server_prices, server_type, None, ipv4_price, ipv6_price
    )

    if server_price_per_hour is not None:
        price_per_second = server_price_per_hour / 3600

    return price_per_second, server_type
