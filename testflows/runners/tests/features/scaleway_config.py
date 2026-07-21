"""Tests for the Scaleway provider: type translation, args validation,
config parsing/propagation, and the canonical-type runner-name round-trip.

No real Scaleway API calls are made; the optional ``scaleway`` SDK is faked via
``mock_scaleway_sdk`` for the factory-construction scenario.  Everything else is
pure logic and needs no SDK.
"""
from argparse import ArgumentTypeError

from testflows.core import *

from testflows.runners.config.parse import parse_config
from testflows.runners.config.factory import provider_factory
from testflows.runners.config.config import (
    Config,
    provider_list,
    scaleway_provider as scaleway_provider_config,
)
from types import SimpleNamespace

from testflows.runners.cloud_provider import (
    ProviderServer,
    ProviderServerType,
)
from testflows.runners.errors import ImageError, ImageSpecFormatError
from testflows.runners.providers.scaleway import utils, args as scw_args
from testflows.runners.scale_up import get_server_types, get_runner_server_type
from testflows.runners.server import get_runner_server_name
from testflows.runners.constants import runner_name_prefix
from testflows.runners.tests.steps.config import write_config
from testflows.runners.tests.steps.scaleway import mock_scaleway_sdk, scaleway_provider


class _FakeImage:
    """Minimal stand-in for a Scaleway Image (private or local marketplace)."""

    def __init__(self, id, name="", arch="x86_64"):
        self.id = id
        self.name = name
        self.arch = arch


# Native Scaleway types (dash-form) <-> canonical (dot-form) used across tests.
_TYPES = [
    ("DEV1-S", "dev1.s"),
    ("GP1-XS", "gp1.xs"),
    ("PRO2-XXS", "pro2.xxs"),
    ("POP2-2C-8G", "pop2.2c.8g"),  # multi-dash
    ("BASIC2-A8C-16G", "basic2.a8c.16g"),  # multi-dash
]


# ---------------------------------------------------------------------------
# Type translation: the dot <-> dash bijection
# ---------------------------------------------------------------------------


@TestScenario
def native_to_canonical(self):
    """Native dash-form translates to dash-free canonical dot-form."""
    for native, canonical in _TYPES:
        assert utils.canonical_type(native) == canonical, native
        assert "-" not in utils.canonical_type(native), native


@TestScenario
def canonical_to_native_roundtrip(self):
    """canonical -> native -> canonical is a clean round-trip (incl. multi-dash)."""
    for native, canonical in _TYPES:
        assert utils.native_type(canonical) == native, canonical
        assert utils.canonical_type(utils.native_type(canonical)) == canonical


# ---------------------------------------------------------------------------
# Scaleway list[str] tags <-> dict labels
# ---------------------------------------------------------------------------


@TestScenario
def tags_dict_roundtrip(self):
    """A label dict round-trips through Scaleway's list[str] tag format."""
    labels = {
        "github-runner": "active",
        "github-runner-label-0": "self-hosted",
        "bare": "",
    }
    tags = utils.dict_to_tags(labels)
    assert "bare" in tags and "bare=" not in tags, tags
    assert utils.tags_to_dict(tags) == labels, tags


# ---------------------------------------------------------------------------
# args validator: the CLI-side guard for the '-' footgun
# ---------------------------------------------------------------------------


@TestScenario
def args_server_type_rejects_dash(self):
    """Dash-form server type is rejected with a message pointing at the dot-form."""
    try:
        scw_args.server_type("DEV1-S")
        assert False, "expected ArgumentTypeError for dash-form type"
    except ArgumentTypeError as exc:
        assert "dev1.s" in str(exc), str(exc)


@TestScenario
def args_server_type_accepts_dot(self):
    """Dot-form server types (incl. multi-dot) are accepted and lower-cased."""
    assert scw_args.server_type("dev1.s") == "dev1.s"
    assert scw_args.server_type("GP1.XS") == "gp1.xs"
    assert scw_args.server_type("pop2.2c.8g") == "pop2.2c.8g"


@TestScenario
def args_zone_validation(self):
    """Zone validator accepts valid zones and rejects malformed ones."""
    assert scw_args.location_type("fr-par-1") == "fr-par-1"
    assert scw_args.location_type("nl-ams-2") == "nl-ams-2"
    try:
        scw_args.location_type("paris")
        assert False, "expected ArgumentTypeError for invalid zone"
    except ArgumentTypeError:
        pass


# ---------------------------------------------------------------------------
# The core concern: canonical types survive the runner-name encoding
# ---------------------------------------------------------------------------


@TestScenario
def get_server_types_accepts_dot_skips_dash(self):
    """type-<dot> labels are honoured; the dash-form is skipped (composite-label rule)."""
    with When("a job carries a dot-form type label"):
        types = get_server_types(["self-hosted", "type-dev1.s"], default="dev1.m")
    with Then("the dot-form type is selected"):
        assert types == ["dev1.s"], types
    with When("a job carries a dash-form type label"):
        types = get_server_types(["type-dev1-s"], default="dev1.m")
    with Then("the dash-form is skipped and the default is used"):
        assert types == ["dev1.m"], types


@TestScenario
def runner_name_roundtrip_for_dot_types(self):
    """A canonical dot-type embedded in a runner name decodes back intact.

    Covers both ``get_runner_server_type`` (remainder capture) and
    ``get_runner_server_name`` (the ``[:5]`` truncation), which only stay
    correct because the canonical form is dash-free.
    """
    for _native, canonical in _TYPES:
        name = f"{runner_name_prefix}run1-0-{canonical}"
        with Then(f"type decodes from name for {canonical}"):
            assert get_runner_server_type(name) == canonical, name
        with And(f"server name reconstructs intact for {canonical}"):
            assert get_runner_server_name(name) == name, name


# ---------------------------------------------------------------------------
# parse_config: propagation and dash rejection
# ---------------------------------------------------------------------------


@TestScenario
def parse_propagates_credentials_and_defaults(self):
    """Scaleway creds and defaults in YAML reach cfg.providers.scaleway."""
    with Given("a config file"):
        path = write_config(yaml_text="""\
            ssh_key: /dev/null
            providers:
              scaleway:
                access_key: SCWTEST
                secret_key: s3cr3t
                project_id: 11111111-1111-1111-1111-111111111111
                defaults:
                  server_type: dev1.m
                  location: fr-par-1
                  image: ubuntu_jammy
        """)
    with When("I parse the config"):
        cfg = parse_config(path)
    with Then("credentials and defaults match"):
        scw = cfg.providers.scaleway
        assert scw.access_key == "SCWTEST", scw
        assert scw.secret_key == "s3cr3t", scw
        assert scw.project_id == "11111111-1111-1111-1111-111111111111", scw
        assert scw.defaults.server_type == "dev1.m", scw
        assert scw.defaults.location == "fr-par-1", scw
        assert scw.defaults.image == "ubuntu_jammy", scw


@TestScenario
def parse_rejects_dash_server_type(self):
    """A dash-form default server_type in config is rejected with guidance."""
    with Given("a config file with a dash-form server_type"):
        path = write_config(yaml_text="""\
            ssh_key: /dev/null
            providers:
              scaleway:
                access_key: AK
                secret_key: SK
                project_id: pid
                defaults:
                  server_type: DEV1-M
        """)
    with Then("parsing raises with a dot-form hint"):
        try:
            parse_config(path)
            assert False, "expected dash-form server_type to be rejected"
        except AssertionError as exc:
            assert "dot-form" in str(exc), str(exc)


# ---------------------------------------------------------------------------
# factory: YAML -> provider_factory -> ScalewayCloudProvider (SDK faked)
# ---------------------------------------------------------------------------


@TestScenario
def factory_builds_scaleway_provider(self):
    """provider_factory constructs a ScalewayCloudProvider with config values."""
    with Given("a faked scaleway SDK"):
        mock_scaleway_sdk()
    with And("a config file"):
        path = write_config(yaml_text="""\
            ssh_key: /dev/null
            providers:
              scaleway:
                access_key: AK
                secret_key: SK
                project_id: proj-123
                defaults:
                  location: nl-ams-1
                  image: ubuntu_jammy
        """)
    with When("I parse and build providers"):
        cfg = parse_config(path)
        providers = provider_factory(cfg)
    with Then("exactly one scaleway provider is returned with config applied"):
        scw = [p for p in providers if p.name == "scaleway"]
        assert len(scw) == 1, [p.name for p in providers]
        provider = scw[0]
        assert provider._project_id == "proj-123"
        assert provider._zone == "nl-ams-1"
        assert provider._default_image == "ubuntu_jammy"
        assert provider.supports_recycling is True


@TestScenario
def ambient_hetzner_token_does_not_override_scaleway(self):
    """An ambient hetzner_token must not auto-wire Hetzner when scaleway is set.

    Reproduces the case where HETZNER_TOKEN is present in the environment but the
    user has explicitly configured providers.scaleway: the factory must build
    only the scaleway provider, not a surprise Hetzner one.
    """
    with Given("a faked scaleway SDK"):
        mock_scaleway_sdk()
    with And("a config with explicit scaleway and an ambient hetzner_token"):
        cfg = Config(github_token="t", github_repository="o/r")
        cfg.hetzner_token = "ambient-hetzner-token"
        cfg.providers = provider_list(
            scaleway=scaleway_provider_config(
                access_key="AK", secret_key="SK", project_id="pid"
            )
        )
    with When("I build providers"):
        providers = provider_factory(cfg)
    with Then("only the scaleway provider is constructed"):
        assert [p.name for p in providers] == ["scaleway"], [p.name for p in providers]


# ---------------------------------------------------------------------------
# get_server_arch: authoritative SDK arch, with a name fallback
# ---------------------------------------------------------------------------


@TestScenario
def get_server_arch_uses_sdk_arch(self):
    """Arch comes from the SDK ServerType.arch, not a name guess.

    Reproduces the ARM instance whose name ('basic2.a8c.16g') does not look ARM
    but whose SDK arch is arm64 — it must resolve to arm64, not x64.
    """
    with Given("a scaleway provider"):
        provider = scaleway_provider()
    with Then("arm/arm64 SDK arch -> arm64 and x86_64 -> x64, regardless of name"):
        for sdk_arch, expected in [("arm64", "arm64"), ("arm", "arm64"), ("x86_64", "x64")]:
            st = ProviderServerType(name="basic2.a8c.16g", _native=SimpleNamespace(arch=sdk_arch))
            assert provider.get_server_arch(st) == expected, (sdk_arch, expected)


@TestScenario
def get_server_arch_defaults_x64_without_sdk_type(self):
    """A bare ProviderServerType (no SDK object) defaults to x64.

    Arch is authoritative from the SDK ServerType; the name is never parsed for
    architecture (Scaleway type names do not reliably encode it).
    """
    with Given("a scaleway provider"):
        provider = scaleway_provider()
    with Then("arch defaults to x64 when there is no SDK ServerType"):
        assert provider.get_server_arch(ProviderServerType(name="dev1.s")) == "x64"
        assert provider.get_server_arch(ProviderServerType(name="basic2.a8c.16g")) == "x64"


@TestScenario
def delete_server_terminates_only(self):
    """delete_server issues a single terminate; volumes are reaped out of band.

    terminate detaches (does not delete) SBS boot volumes; deleting them inline
    is neither atomic nor crash-safe, so the boot volume is tagged at create and
    reclaimed later by reap_orphaned_volumes.
    """
    with Given("a scaleway provider"):
        provider = scaleway_provider()
    with And("a server with an SBS boot volume"):
        native = SimpleNamespace(volumes={
            "0": SimpleNamespace(id="vol-sbs", volume_type="sbs_volume", boot=True),
        })
        server = ProviderServer(
            id="srv-1", name="github-runner-1-0-dev1.s", status="off",
            public_ipv4=None, private_ipv4=None, labels={},
            server_type="dev1.s", location="fr-par-1", created=None, _native=native,
        )
    with When("delete_server is called"):
        provider.delete_server(server)
    with Then("it terminates and does not delete volumes inline"):
        _, kwargs = provider._instance.server_action.call_args
        assert str(kwargs["action"]) == "terminate", kwargs["action"]
        provider._block.delete_volume.assert_not_called()


def _running_native(name="github-runner-1-0"):
    """A minimal Scaleway-native running server for _server_to_provider."""
    return SimpleNamespace(
        id="srv-1", name=name, state="running", zone="fr-par-1",
        commercial_type="basic2-a16c-32g", tags=[],
        public_ips=None, public_ip=None, private_ip=None,
    )


@TestScenario
def create_server_builds_tagged_boot_volume(self):
    """create_server pre-creates a tagged SBS boot volume and attaches it by id."""
    with Given("a scaleway provider"):
        provider = scaleway_provider()
    with And("an SBS custom image and stubbed volume/instance calls"):
        provider._instance.get_image.return_value = SimpleNamespace(
            image=SimpleNamespace(
                root_volume=SimpleNamespace(id="snap-1", volume_type="sbs_snapshot")
            )
        )
        provider._block.get_snapshot.return_value = SimpleNamespace(size=128849018880)
        provider._block.create_volume.return_value = SimpleNamespace(id="vol-boot")
        provider._instance._create_server.return_value = SimpleNamespace(
            server=SimpleNamespace(id="srv-1")
        )
        provider._wait_for_state = lambda *a, **k: _running_native()
    with When("create_server runs"):
        provider.create_server(
            name="github-runner-1-0",
            server_type=ProviderServerType(name="basic2-a16c-32g"),
            location="fr-par-1", image="img-uuid", ssh_keys=[],
            labels={"github-runner": "active"},
        )
    with Then("the boot volume is created from the snapshot and tagged at birth"):
        ckw = provider._block.create_volume.call_args.kwargs
        assert "github-runner-volume=active" in ckw["tags"], ckw
        assert ckw["from_snapshot"].snapshot_id == "snap-1", ckw
    with And("the instance is created from that volume, not an image"):
        skw = provider._instance._create_server.call_args.kwargs
        assert skw.get("image") is None, skw
        template = skw["volumes"]["0"]
        assert template.id == "vol-boot", template
        assert template.boot is True, template


@TestScenario
def create_server_local_snapshot_raises_helpful_error(self):
    """A local (l_ssd) root volume yields a helpful error and creates no volume."""
    with Given("a scaleway provider"):
        provider = scaleway_provider()
    with And("an image whose root volume is a local snapshot"):
        provider._instance.get_image.return_value = SimpleNamespace(
            image=SimpleNamespace(
                root_volume=SimpleNamespace(id="snap-local", volume_type="l_ssd")
            )
        )
    with Then("create_server raises ImageError and creates no volume"):
        try:
            provider.create_server(
                name="r", server_type=ProviderServerType(name="basic2-a16c-32g"),
                location="fr-par-1", image="img-local", ssh_keys=[], labels={},
            )
            assert False, "expected ImageError for a non-SBS image"
        except ImageError as exc:
            assert "SBS" in str(exc), exc
        provider._block.create_volume.assert_not_called()


@TestScenario
def create_server_cross_project_snapshot_raises_helpful_error(self):
    """A 403 from create_volume (marketplace/public snapshot) maps to a helpful error."""
    with Given("a scaleway provider"):
        provider = scaleway_provider()
    # Imported after the fixture installs the faked scaleway_core into sys.modules.
    from scaleway_core.api import ScalewayException

    with And("an SBS-typed image whose snapshot is in another project"):
        provider._instance.get_image.return_value = SimpleNamespace(
            image=SimpleNamespace(
                root_volume=SimpleNamespace(id="snap-x", volume_type="sbs_snapshot")
            )
        )
        provider._block.get_snapshot.side_effect = ScalewayException(status_code=403)
        provider._block.create_volume.side_effect = ScalewayException(status_code=403)
    with Then("create_server raises ImageError mentioning the project"):
        try:
            provider.create_server(
                name="r", server_type=ProviderServerType(name="basic2-a16c-32g"),
                location="fr-par-1", image="img-marketplace", ssh_keys=[], labels={},
            )
            assert False, "expected ImageError for a cross-project snapshot"
        except ImageError as exc:
            assert "project" in str(exc), exc


@TestScenario
def create_server_powering_on_failure_terminates_partial_instance(self):
    """If power-on/boot fails, the partial instance is terminated (volume reaper-covered)."""
    with Given("a scaleway provider"):
        provider = scaleway_provider()
    with And("a created boot volume + instance, but boot never reaches running"):
        provider._instance.get_image.return_value = SimpleNamespace(
            image=SimpleNamespace(
                root_volume=SimpleNamespace(id="snap-1", volume_type="sbs_snapshot")
            )
        )
        provider._block.get_snapshot.return_value = SimpleNamespace(size=10)
        provider._block.create_volume.return_value = SimpleNamespace(id="vol-boot")
        provider._instance._create_server.return_value = SimpleNamespace(
            server=SimpleNamespace(id="srv-1")
        )

        def _boom(*a, **k):
            raise RuntimeError("boot timeout")

        provider._wait_for_state = _boom
    with Then("it best-effort terminates the partial instance and re-raises"):
        try:
            provider.create_server(
                name="r", server_type=ProviderServerType(name="basic2-a16c-32g"),
                location="fr-par-1", image="img", ssh_keys=[], labels={},
            )
            assert False, "expected the boot failure to propagate"
        except RuntimeError:
            pass
        actions = [
            str(c.kwargs.get("action"))
            for c in provider._instance.server_action.call_args_list
        ]
        assert "terminate" in actions, actions


@TestScenario
def reap_orphaned_volumes_deletes_detached_aged_only(self):
    """The reaper deletes detached, aged, tagged volumes; not attached or fresh ones."""
    from datetime import datetime, timezone, timedelta

    with Given("a scaleway provider"):
        provider = scaleway_provider()
    with And("tagged volumes: detached+old, attached, and just-detached"):
        now = datetime.now(timezone.utc)
        old = now - timedelta(minutes=10)
        provider._block.list_volumes_all.return_value = [
            SimpleNamespace(id="orphan", references=[], last_detached_at=old, created_at=old),
            SimpleNamespace(id="attached", references=[SimpleNamespace(id="r")],
                            last_detached_at=None, created_at=old),
            SimpleNamespace(id="fresh", references=[], last_detached_at=now, created_at=now),
        ]
    with When("the scale-down post-cycle hook runs"):
        provider.after_scale_down()
    with Then("it lists tagged, non-deleted volumes"):
        _, lkwargs = provider._block.list_volumes_all.call_args
        assert lkwargs.get("include_deleted") is False, lkwargs
        assert "github-runner-volume=active" in (lkwargs.get("tags") or []), lkwargs
    with And("only the detached, aged orphan is deleted"):
        assert provider._block.delete_volume.call_count == 1, provider._block.delete_volume.call_count
        _, vkwargs = provider._block.delete_volume.call_args
        assert vkwargs["volume_id"] == "orphan", vkwargs


@TestScenario
def scale_up_hook_does_not_reap_volumes(self):
    with Given("a scaleway provider"):
        provider = scaleway_provider()
    provider.before_scale_up(frozenset())
    provider._block.list_volumes_all.assert_not_called()
    provider._block.delete_volume.assert_not_called()


@TestScenario
def scale_down_maintenance_failure_is_non_fatal(self):
    with Given("a scaleway provider"):
        provider = scaleway_provider()
    with And("maintenance raises unexpectedly"):
        provider._reap_orphaned_volumes = lambda: (_ for _ in ()).throw(
            RuntimeError("boom")
        )
    with Then("after_scale_down raises and is handled by orchestration"):
        try:
            provider.after_scale_down()
        except RuntimeError:
            pass
        else:
            assert False, "expected maintenance failure to propagate"


@TestScenario
def stopped_in_place_space_form_maps_to_off(self):
    """The API's space-form 'stopped in place' must map to OFF, not UNKNOWN.

    Scaleway returns the state with spaces; the SDK enum's declared value uses
    underscores and passes unknown values through as raw strings. If we don't
    normalize, the instance maps to STATUS_UNKNOWN, is excluded from listings,
    and is never reaped (quota leak).
    """
    from types import SimpleNamespace
    from testflows.runners.providers.scaleway import utils
    from testflows.runners.cloud_provider import CloudProvider

    with Then("state_key normalizes spaces to underscores"):
        assert utils.state_key("stopped in place") == "stopped_in_place"
        assert utils.state_key("STOPPED IN PLACE") == "stopped_in_place"
    with And("a server in the space-form state maps to OFF and is listable"):
        srv = SimpleNamespace(
            id="i", name="github-runner-1-0-dev1.s", state="stopped in place",
            zone="fr-par-1", commercial_type="DEV1-S",
            public_ips=[], public_ip=None, private_ip=None, tags=[], creation_date=None,
        )
        ps = utils._server_to_provider(srv)
        assert ps.status == CloudProvider.STATUS_OFF, ps.status
        assert utils.state_key(srv.state) in utils._ACTIVE_STATES


@TestScenario
def get_server_ssh_key_name_round_trips(self):
    """get_server_ssh_key_name reads back the key name build_server_labels stored.

    Scaleway inherits the base 'github-runner-ssh-key' tag; this guards the label
    divergence that broke the scale_down ownership check for non-Hetzner servers.
    """
    with Given("a scaleway provider"):
        provider = scaleway_provider()
    with When("build_server_labels records an ssh key name"):
        labels = provider.build_server_labels(["self-hosted"], ssh_key_name="abc123")
        server = ProviderServer(
            id="i", name="github-runner-1-0-dev1.s", status="off",
            public_ipv4=None, private_ipv4=None, labels=labels,
            server_type="dev1.s", location="fr-par-1", created=None,
        )
    with Then("get_server_ssh_key_name returns that name"):
        assert provider.get_server_ssh_key_name(server) == "abc123"


# ---------------------------------------------------------------------------
# get_image: UUID / marketplace label / custom image by name
# ---------------------------------------------------------------------------


@TestScenario
def get_image_uuid_passthrough(self):
    """An image UUID is returned as-is without any API lookup."""
    with Given("a scaleway provider"):
        provider = scaleway_provider()
    with Then("a UUID resolves to itself"):
        uid = "33333333-3333-3333-3333-333333333333"
        assert provider.get_image(uid) == uid


@TestScenario
def get_image_rejects_foreign_specs(self):
    """Hetzner colon-form and AWS ami- specs raise ImageSpecFormatError."""
    with Given("a scaleway provider"):
        provider = scaleway_provider()
    with Then("foreign specs are flagged so scale_up can try another provider"):
        for foreign in ("x86:system:ubuntu-22.04", "ami-0abc123def", "resolve:ssm:/x"):
            try:
                provider.get_image(foreign)
                assert False, f"expected ImageSpecFormatError for {foreign!r}"
            except ImageSpecFormatError:
                pass


@TestScenario
def get_image_marketplace_label(self):
    """A marketplace label resolves before custom images are consulted."""
    with Given("a scaleway provider"):
        provider = scaleway_provider()
    with And("marketplace resolution returns an id and custom lookup would fail"):
        provider._resolve_marketplace_image = lambda label: "mkt-" + label

        def _boom(**kwargs):
            raise AssertionError("custom lookup must not run when marketplace matches")

        provider._instance.list_images_all = _boom
    with Then("the marketplace id is returned"):
        assert provider.get_image("ubuntu_jammy") == "mkt-ubuntu_jammy"


@TestScenario
def get_image_custom_dashed_name_skips_marketplace(self):
    """A custom name with '-'/'.' skips the marketplace lookup entirely.

    Reproduces the reported bug: a baked image name like
    'arm-ubuntu-24.04-regression-tester' must not be sent to the marketplace
    endpoint (which 404s), but resolved directly as a custom image.
    """
    with Given("a scaleway provider"):
        provider = scaleway_provider()
    with And("marketplace resolution would fail if called, and a private image exists"):
        def _must_not_call(label):
            raise AssertionError("marketplace must not be queried for a dashed name")

        provider._resolve_marketplace_image = _must_not_call
        provider._instance.list_images_all = lambda **kwargs: [
            _FakeImage(id="img-arm", name="arm-ubuntu-24.04-regression-tester", arch="arm64"),
        ]
    with Then("the custom image id is returned without touching the marketplace"):
        assert provider.get_image("arm-ubuntu-24.04-regression-tester") == "img-arm"


@TestScenario
def get_image_marketplace_shaped_miss_falls_through_to_custom(self):
    """A marketplace-shaped spec that misses (None) falls through to custom images."""
    with Given("a scaleway provider"):
        provider = scaleway_provider()
    with And("marketplace returns None (unknown label) and a custom image matches"):
        provider._resolve_marketplace_image = lambda label: None
        provider._instance.list_images_all = lambda **kwargs: [
            _FakeImage(id="img-x86", name="ubuntucustom", arch="x86_64"),
        ]
    with Then("the custom image id is returned"):
        assert provider.get_image("ubuntucustom") == "img-x86"


@TestScenario
def get_image_custom_by_name(self):
    """A custom image name resolves to its private-image UUID, preferring x86_64."""
    with Given("a scaleway provider"):
        provider = scaleway_provider()
    with And("no marketplace match, and a private image exists in two arches"):
        provider._resolve_marketplace_image = lambda label: None
        provider._instance.list_images_all = lambda **kwargs: [
            _FakeImage(id="img-arm", name="runner-base", arch="arm64"),
            _FakeImage(id="img-x86", name="runner-base", arch="x86_64"),
        ]
    with Then("the x86_64 custom image id is returned"):
        assert provider.get_image("runner-base") == "img-x86"


@TestScenario
def get_image_custom_name_requires_exact_match(self):
    """A prefix-only name match is rejected (the API name filter is a prefix)."""
    with Given("a scaleway provider"):
        provider = scaleway_provider()
    with And("marketplace misses and only a prefix-match private image exists"):
        provider._resolve_marketplace_image = lambda label: None
        provider._instance.list_images_all = lambda **kwargs: [
            _FakeImage(id="img-1", name="runner-base-2024", arch="x86_64"),
        ]
    with Then("get_image raises ImageError (no exact name match)"):
        try:
            provider.get_image("runner-base")
            assert False, "expected ImageError for prefix-only match"
        except ImageError:
            pass


@TestScenario
def provider_sdk_calls_match_real_signatures(self):
    """Every provider SDK call binds against the real scaleway SDK signatures.

    The other tests mock the SDK with MagicMocks, which do NOT enforce argument
    signatures — so a missing required kwarg (e.g. list_volumes_all's
    include_deleted) passes the mocks but fails at runtime. This binds the exact
    kwargs the provider passes to the real signatures. Skips when the optional
    scaleway SDK is not installed.
    """
    import inspect

    try:
        from scaleway.instance.v1 import InstanceV1API
        from scaleway.block.v1 import BlockV1API
        from scaleway.marketplace.v2 import MarketplaceV2API
        from scaleway.iam.v1alpha1 import IamV1Alpha1API
    except ImportError:
        return

    calls = [
        (InstanceV1API, "_create_server", dict(zone="z", name="n", commercial_type="t", dynamic_ip_required=True, protected=False, tags=[], project="p", volumes={})),
        (InstanceV1API, "server_action", dict(server_id="s", zone="z", action="terminate")),
        (InstanceV1API, "_update_server", dict(server_id="s", zone="z", name="n", tags=[])),
        (InstanceV1API, "get_server", dict(server_id="s", zone="z")),
        (InstanceV1API, "get_image", dict(image_id="i", zone="z")),
        (InstanceV1API, "list_servers_all", dict(zone="z", tags=["x"])),
        (InstanceV1API, "list_servers_types", dict(zone="z")),
        (InstanceV1API, "list_images_all", dict(zone="z", name="n", public=False, project="p")),
        (BlockV1API, "create_volume", dict(zone="z", name="n", project_id="p", tags=[], from_snapshot=None)),
        (BlockV1API, "get_snapshot", dict(snapshot_id="s", zone="z")),
        (BlockV1API, "wait_for_volume", dict(volume_id="v", zone="z")),
        (BlockV1API, "delete_volume", dict(volume_id="v", zone="z")),
        (BlockV1API, "list_volumes_all", dict(zone="z", tags=["x"], include_deleted=False)),
        (MarketplaceV2API, "list_local_images_all", dict(image_label="l", zone="z", type_="instance_local")),
        (IamV1Alpha1API, "list_ssh_keys_all", dict(project_id="p")),
        (IamV1Alpha1API, "create_ssh_key", dict(name="n", public_key="k", project_id="p")),
    ]
    with Then("every provider SDK call binds to the real signature"):
        for api, meth, kw in calls:
            sig = inspect.signature(getattr(api, meth))
            # None stands in for self; raises TypeError if a required arg is missing
            sig.bind(None, **kw)


# ---------------------------------------------------------------------------
# Feature entry point
# ---------------------------------------------------------------------------


@TestFeature
@Name("scaleway config")
def feature(self):
    """Scaleway type translation, args validation, config parsing, and factory."""
    for scenario in loads(current_module(), Scenario):
        scenario()
