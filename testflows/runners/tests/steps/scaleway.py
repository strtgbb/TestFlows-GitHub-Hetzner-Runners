"""Shared @TestStep(Given) fixtures for Scaleway-provider tests.

The ``scaleway`` SDK is an optional dependency and is not required to run the
test suite, so instead of patching it we inject lightweight fake modules into
``sys.modules`` for the duration of the step.  This lets ``ScalewayCloudProvider``
construct (its ``__init__`` does ``from scaleway import Client`` and
``from scaleway.instance.v1 import InstanceV1API``) without the real SDK.
"""
import sys
import types
from unittest.mock import MagicMock

from testflows.core import *


@TestStep(Given)
def mock_scaleway_sdk(self):
    """Install fake ``scaleway`` SDK modules into sys.modules and yield them.

    Restores any previously-present modules on exit.
    """
    names = [
        "scaleway",
        "scaleway.instance",
        "scaleway.instance.v1",
        "scaleway.block",
        "scaleway.block.v1",
        "scaleway.iam",
        "scaleway.iam.v1alpha1",
        "scaleway.marketplace",
        "scaleway.marketplace.v2",
    ]
    saved = {name: sys.modules.get(name) for name in names}
    try:
        scaleway_mod = types.ModuleType("scaleway")
        scaleway_mod.Client = MagicMock(name="Client")

        instance_mod = types.ModuleType("scaleway.instance.v1")
        instance_mod.InstanceV1API = MagicMock(name="InstanceV1API")

        class ServerAction:
            POWERON = "poweron"
            POWEROFF = "poweroff"
            TERMINATE = "terminate"

        instance_mod.ServerAction = ServerAction

        block_mod = types.ModuleType("scaleway.block.v1")
        block_mod.BlockV1API = MagicMock(name="BlockV1API")

        iam_mod = types.ModuleType("scaleway.iam.v1alpha1")
        iam_mod.IamV1Alpha1API = MagicMock(name="IamV1Alpha1API")

        marketplace_mod = types.ModuleType("scaleway.marketplace.v2")
        marketplace_mod.MarketplaceV2API = MagicMock(name="MarketplaceV2API")

        sys.modules["scaleway"] = scaleway_mod
        sys.modules["scaleway.instance"] = types.ModuleType("scaleway.instance")
        sys.modules["scaleway.instance.v1"] = instance_mod
        sys.modules["scaleway.block"] = types.ModuleType("scaleway.block")
        sys.modules["scaleway.block.v1"] = block_mod
        sys.modules["scaleway.iam"] = types.ModuleType("scaleway.iam")
        sys.modules["scaleway.iam.v1alpha1"] = iam_mod
        sys.modules["scaleway.marketplace"] = types.ModuleType("scaleway.marketplace")
        sys.modules["scaleway.marketplace.v2"] = marketplace_mod

        yield scaleway_mod
    finally:
        for name, mod in saved.items():
            if mod is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = mod


@TestStep(Given)
def scaleway_provider(self):
    """Yield a ScalewayCloudProvider built against the faked SDK.

    Construction uses the faked ``scaleway`` modules, so ``provider._instance``
    is a MagicMock whose methods callers can stub per-test.
    """
    with Given("a faked scaleway SDK"):
        mock_scaleway_sdk()

    from testflows.runners.providers.scaleway.provider import ScalewayCloudProvider

    provider = ScalewayCloudProvider(
        access_key="SCWTESTKEY",
        secret_key="11111111-1111-1111-1111-111111111111",
        project_id="22222222-2222-2222-2222-222222222222",
        zone="fr-par-1",
        default_image_spec="ubuntu_jammy",
        ssh_user="root",
    )
    yield provider
