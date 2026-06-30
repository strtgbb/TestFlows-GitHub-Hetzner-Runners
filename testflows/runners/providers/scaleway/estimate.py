"""Scaleway cost estimation implementation.

Scaleway CPU Instances are billed per hour (1-hour minimum increment), and
compute billing pauses while an Instance is powered off.  The
``list_servers_types`` endpoint returns an ``hourly_price`` per commercial type,
which we key by the canonical dot-form so ``get_runner_server_type`` (which
decodes the runner name to the canonical form) hits the map.
"""

from github import Github
from github.Repository import Repository

from ...actions import Action
from ...config import Config
from ...utils import get_runner_server_type
from .utils import canonical_type


def check_prices(client, zones: list[str] = None) -> dict[str, dict[str, float]]:
    """Return a mapping of canonical_type -> zone -> hourly_price.

    ``list_servers_types`` reports prices keyed by the native dash-form
    commercial type (``DEV1-S``); we translate each key to the canonical
    dot-form (``dev1.s``) so the rest of the estimation path is consistent.
    """
    from scaleway.instance.v1 import InstanceV1API

    api = InstanceV1API(client)
    zones = zones or [getattr(client, "default_zone", None) or "fr-par-1"]

    prices: dict[str, dict[str, float]] = {}
    for zone in zones:
        response = api.list_servers_types(zone=zone)
        for native, server_type in (getattr(response, "servers", None) or {}).items():
            hourly = getattr(server_type, "hourly_price", None)
            if hourly:
                prices.setdefault(canonical_type(native), {})[str(zone)] = float(hourly)
    return prices


def get_server_price(
    server_prices: dict[str, dict[str, float]],
    server_type: str,
    server_location: str,
    ipv4_price: float = 0.0,
    ipv6_price: float = 0.0,
) -> float:
    """Get hourly price for a Scaleway instance type.

    ``server_location`` may be a specific zone or None; when None (the path used
    by ``get_runner_server_price_per_second``) we return the first zone's price
    for the type, since Scaleway hourly prices do not vary across zones for the
    same commercial type.
    """
    try:
        zone_prices = server_prices[server_type]
    except (KeyError, TypeError):
        return None
    if server_location is not None and server_location in zone_prices:
        return zone_prices[server_location]
    return next(iter(zone_prices.values()), None)


def get_runner_server_price_per_second(
    server_prices: dict[str, dict[str, float]],
    runner_name: str,
    ipv4_price: float = 0.0,
    ipv6_price: float = 0.0,
) -> tuple[float, str]:
    """Get runner server price per second for Scaleway."""
    price_per_second = None

    server_type = get_runner_server_type(runner_name)
    server_price_per_hour = get_server_price(server_prices, server_type, None)

    if server_price_per_hour is not None:
        price_per_second = server_price_per_hour / 3600

    return price_per_second, server_type


def login_and_get_prices(
    args, config: Config
) -> tuple[Repository, dict[str, dict[str, float]]]:
    """Login to GitHub and fetch hourly Scaleway instance prices."""
    from scaleway import Client

    config.check("github_token")
    config.check("github_repository")

    scw = config.providers.scaleway
    zone = (scw.defaults.location if scw else None) or "fr-par-1"

    with Action("Logging in to Scaleway"):
        client = Client(
            access_key=scw.access_key,
            secret_key=scw.secret_key,
            default_project_id=scw.project_id,
            default_organization_id=scw.organization_id,
            default_zone=zone,
        )

    with Action("Logging in to GitHub"):
        github_client = Github(login_or_token=config.github_token, per_page=100)

    with Action(f"Getting repository {config.github_repository}"):
        repo: Repository = github_client.get_repo(config.github_repository)

    with Action(f"Getting Scaleway instance prices for {zone}"):
        server_prices = check_prices(client, zones=[zone])

    return (repo, server_prices)
