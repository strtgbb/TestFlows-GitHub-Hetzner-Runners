"""Provider factory: construct CloudProvider instances from Config."""

from .config import Config
from ..cloud_provider import CloudProvider
from ..providers.hetzner.provider import HetznerCloudProvider
from ..providers.scaleway.provider import ScalewayCloudProvider
from ..providers.aws.provider import AWSCloudProvider
from ..providers.dedicated_static.provider import DedicatedStaticCloudProvider

# The single per-provider seam. A future auto-discovery step replaces this list.
PROVIDER_REGISTRY: list[type[CloudProvider]] = sorted(
    [HetznerCloudProvider, ScalewayCloudProvider, AWSCloudProvider,
     DedicatedStaticCloudProvider],
    key=lambda c: c.precedence,
)


def provider_factory(config: Config) -> list[CloudProvider]:
    """Construct every configured provider, in precedence order."""
    return [p for cls in PROVIDER_REGISTRY if (p := cls.from_config(config)) is not None]
