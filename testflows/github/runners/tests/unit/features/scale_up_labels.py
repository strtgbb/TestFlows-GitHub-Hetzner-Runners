"""Tests for label-parsing helpers in scale_up.py.

All functions under test are pure (no I/O, no API calls).
"""
from unittest.mock import MagicMock

from testflows.core import *

from hcloud.server_types.domain import ServerType

from testflows.github.runners.scale_up import (
    _resolve_provider,
    _resolve_locations,
    expand_meta_label,
    get_server_locations,
    get_server_net_config,
    get_server_types,
    get_server_image,
    get_server_volumes,
    get_server_disk_size,
    get_server_provider,
    parse_volume_size,
)
from testflows.github.runners.cloud_provider import ProviderServerType
from testflows.github.runners.errors import (
    ServerTypeError,
    ImageSpecFormatError,
    LocationError,
)
from testflows.github.runners.providers.hetzner.provider import HetznerCloudProvider


# ---------------------------------------------------------------------------
# parse_volume_size
# ---------------------------------------------------------------------------


@TestScenario
def parse_volume_size_plain_integer(self):
    assert parse_volume_size("20", 10) == 20


@TestScenario
def parse_volume_size_gb_suffix(self):
    assert parse_volume_size("50GB", 10) == 50


@TestScenario
def parse_volume_size_gb_case_insensitive(self):
    assert parse_volume_size("50gb", 10) == 50


@TestScenario
def parse_volume_size_invalid_returns_default(self):
    assert parse_volume_size("notanumber", 10) == 10


@TestScenario
def parse_volume_size_empty_returns_default(self):
    assert parse_volume_size("", 15) == 15


@TestScenario
def parse_volume_size_negative_returns_abs(self):
    assert parse_volume_size("-20", 10) == 20


# ---------------------------------------------------------------------------
# get_server_arch (canonical impl on HetznerCloudProvider)
# ---------------------------------------------------------------------------


def _arch(name):
    return HetznerCloudProvider.get_server_arch(None, ProviderServerType(name=name))


@TestScenario
def get_server_arch_x64_for_standard(self):
    assert _arch("cx23") == "x64"


@TestScenario
def get_server_arch_arm64_for_cax(self):
    assert _arch("cax11") == "arm64"


@TestScenario
def get_server_arch_arm64_uppercase(self):
    assert _arch("CAX31") == "arm64"


@TestScenario
def get_server_arch_x64_for_ccx(self):
    assert _arch("ccx13") == "x64"


# ---------------------------------------------------------------------------
# get_server_types
# ---------------------------------------------------------------------------


@TestScenario
def get_server_types_single(self):
    assert get_server_types(["type-cx23"], default="cx11") == ["cx23"]


@TestScenario
def get_server_types_multiple(self):
    assert get_server_types(["type-cx23", "type-cx33"], default="cx11") == ["cx23", "cx33"]


@TestScenario
def get_server_types_falls_back_to_default(self):
    assert get_server_types(["self-hosted", "linux"], default="cx11") == ["cx11"]


@TestScenario
def get_server_types_default_servertype_object(self):
    default = ServerType(name="cx11")
    assert get_server_types(["self-hosted"], default=default) == ["cx11"]


@TestScenario
def get_server_types_composite_skipped(self):
    assert get_server_types(["type-cx23-cx33"], default="cx11") == ["cx11"]


@TestScenario
def get_server_types_label_prefix(self):
    assert get_server_types(["myprefix-type-cx23"], default="cx11", label_prefix="myprefix") == ["cx23"]


@TestScenario
def get_server_types_case_insensitive(self):
    assert get_server_types(["TYPE-CX23"], default="cx11") == ["cx23"]


@TestScenario
def get_server_types_unrelated_labels_ignored(self):
    assert get_server_types(["self-hosted", "linux", "type-cx23"], default="cx11") == ["cx23"]


# ---------------------------------------------------------------------------
# get_server_locations
# ---------------------------------------------------------------------------


@TestScenario
def get_server_locations_single(self):
    assert get_server_locations(["in-nbg1"]) == ["nbg1"]


@TestScenario
def get_server_locations_multiple(self):
    assert get_server_locations(["in-nbg1", "in-fsn1"]) == ["nbg1", "fsn1"]


@TestScenario
def get_server_locations_default(self):
    assert get_server_locations(["self-hosted"], default="hel1") == ["hel1"]


@TestScenario
def get_server_locations_default_none(self):
    assert get_server_locations(["self-hosted"]) == [None]


@TestScenario
def get_server_locations_aws_az(self):
    """AWS AZ names like us-east-1a contain dashes and must not be dropped."""
    assert get_server_locations(["in-us-east-1a"]) == ["us-east-1a"]


@TestScenario
def get_server_locations_composite_passthrough(self):
    assert get_server_locations(["in-hel1-fsn1-nbg1"], default="nbg1") == ["hel1-fsn1-nbg1"]


@TestScenario
def get_server_locations_label_prefix(self):
    assert get_server_locations(["myprefix-in-nbg1"], label_prefix="myprefix") == ["nbg1"]


@TestScenario
def get_server_locations_case_insensitive(self):
    assert get_server_locations(["IN-NBG1"]) == ["nbg1"]


# ---------------------------------------------------------------------------
# get_server_volumes
# ---------------------------------------------------------------------------


@TestScenario
def get_server_volumes_no_size(self):
    result = get_server_volumes(["volume-mydata"], default=10)
    assert len(result) == 1
    assert result[0].name == "mydata"
    assert result[0].size == 10


@TestScenario
def get_server_volumes_explicit_size(self):
    result = get_server_volumes(["volume-mydata-20"], default=10)
    assert result[0].size == 20


@TestScenario
def get_server_volumes_gb_suffix(self):
    result = get_server_volumes(["volume-mydata-50gb"], default=10)
    assert result[0].size == 50


@TestScenario
def get_server_volumes_multiple(self):
    result = get_server_volumes(["volume-data-20", "volume-cache-5"], default=10)
    names = {v.name: v.size for v in result}
    assert names == {"data": 20, "cache": 5}


@TestScenario
def get_server_volumes_duplicate_last_wins(self):
    result = get_server_volumes(["volume-data-20", "volume-data-30"], default=10)
    assert len(result) == 1
    assert result[0].size == 30


@TestScenario
def get_server_volumes_empty(self):
    assert get_server_volumes(["self-hosted", "linux"]) == []


@TestScenario
def get_server_volumes_label_prefix(self):
    result = get_server_volumes(["myprefix-volume-mydata-20"], default=10, label_prefix="myprefix")
    assert result[0].name == "mydata"
    assert result[0].size == 20


# ---------------------------------------------------------------------------
# get_server_net_config
# ---------------------------------------------------------------------------


@TestScenario
def get_server_net_config_defaults_both(self):
    cfg = get_server_net_config([])
    assert cfg.enable_ipv4 is True
    assert cfg.enable_ipv6 is True


@TestScenario
def get_server_net_config_ipv4_only(self):
    cfg = get_server_net_config(["net-ipv4"])
    assert cfg.enable_ipv4 is True
    assert cfg.enable_ipv6 is False


@TestScenario
def get_server_net_config_ipv6_only(self):
    cfg = get_server_net_config(["net-ipv6"])
    assert cfg.enable_ipv4 is False
    assert cfg.enable_ipv6 is True


@TestScenario
def get_server_net_config_both_explicit(self):
    cfg = get_server_net_config(["net-ipv4", "net-ipv6"])
    assert cfg.enable_ipv4 is True
    assert cfg.enable_ipv6 is True


@TestScenario
def get_server_net_config_label_prefix(self):
    cfg = get_server_net_config(["myprefix-net-ipv4"], label_prefix="myprefix")
    assert cfg.enable_ipv4 is True
    assert cfg.enable_ipv6 is False


# ---------------------------------------------------------------------------
# expand_meta_label
# ---------------------------------------------------------------------------


@TestScenario
def expand_meta_label_empty(self):
    assert expand_meta_label({}, ["self-hosted", "linux"]) == ["self-hosted", "linux"]


@TestScenario
def expand_meta_label_known(self):
    meta = {"standard-linux": {"type-cx23", "self-hosted"}}
    result = expand_meta_label(meta, ["standard-linux"])
    assert "standard-linux" in result
    assert "type-cx23" in result
    assert "self-hosted" in result


@TestScenario
def expand_meta_label_composite_type(self):
    result = expand_meta_label({}, ["type-cx23-cx33"])
    assert "type-cx23" in result
    assert "type-cx33" in result


@TestScenario
def expand_meta_label_composite_in(self):
    result = expand_meta_label({}, ["in-nbg1-fsn1"])
    assert "in-nbg1" in result
    assert "in-fsn1" in result


@TestScenario
def expand_meta_label_original_preserved(self):
    result = expand_meta_label({}, ["type-cx23-cx33"])
    assert "type-cx23-cx33" in result


@TestScenario
def expand_meta_label_deduplication(self):
    meta = {"standard-linux": {"type-cx23"}}
    result = expand_meta_label(meta, ["standard-linux", "type-cx23"])
    assert result.count("type-cx23") == 1


@TestScenario
def expand_meta_label_with_prefix(self):
    result = expand_meta_label({}, ["myprefix-type-cx23-cx33"], label_prefix="myprefix")
    assert "myprefix-type-cx23" in result
    assert "myprefix-type-cx33" in result


@TestScenario
def expand_meta_label_empty_labels(self):
    assert expand_meta_label({}, []) == []


# ---------------------------------------------------------------------------
# get_server_image
# ---------------------------------------------------------------------------


def _provider_returning(image):
    p = MagicMock()
    p.get_image.return_value = image
    return p


@TestScenario
def get_server_image_matches_label(self):
    img = MagicMock()
    provider = _provider_returning(img)
    result = get_server_image(provider, ["image-x86-system-ubuntu-22.04"], default=None)
    provider.get_image.assert_called_once()
    assert result is img


@TestScenario
def get_server_image_default_when_no_label(self):
    default = MagicMock()
    provider = MagicMock()
    result = get_server_image(provider, ["self-hosted", "linux"], default=default)
    provider.get_image.assert_not_called()
    assert result is default


@TestScenario
def get_server_image_last_wins(self):
    img1, img2 = MagicMock(), MagicMock()
    provider = MagicMock()
    provider.get_image.side_effect = [img1, img2]
    result = get_server_image(
        provider,
        ["image-x86-system-ubuntu-22.04", "image-x86-system-ubuntu-24.04"],
        default=None,
    )
    assert provider.get_image.call_count == 2
    assert result is img2


@TestScenario
def get_server_image_label_prefix(self):
    img = MagicMock()
    provider = _provider_returning(img)
    result = get_server_image(
        provider,
        ["myprefix-image-x86-system-ubuntu-22.04"],
        default=None,
        label_prefix="myprefix",
    )
    provider.get_image.assert_called_once()
    assert result is img


@TestScenario
def get_server_image_skips_wrong_format(self):
    """Provider raises ImageSpecFormatError for foreign spec; next label is used."""
    img = MagicMock()
    provider = MagicMock()
    provider.get_image.side_effect = [ImageSpecFormatError("wrong format"), img]
    result = get_server_image(
        provider,
        ["image-ami-0abcdef1234567890", "image-x86-system-ubuntu-22.04"],
        default=None,
    )
    assert provider.get_image.call_count == 2
    assert result is img


@TestScenario
def get_server_image_all_wrong_format_uses_default(self):
    default = MagicMock()
    provider = MagicMock()
    provider.get_image.side_effect = ImageSpecFormatError("wrong format")
    result = get_server_image(
        provider, ["image-ami-0abcdef1234567890"], default=default
    )
    assert result is default


# ---------------------------------------------------------------------------
# _resolve_provider
# ---------------------------------------------------------------------------


def _supporting_provider():
    p = MagicMock()
    p.get_server_type.return_value = MagicMock()
    return p


def _non_supporting_provider():
    p = MagicMock()
    p.get_server_type.side_effect = ServerTypeError("unknown type")
    return p


@TestScenario
def resolve_provider_single_match(self):
    provider = _supporting_provider()
    resolved_p, _ = _resolve_provider("cx22", [provider])
    assert resolved_p is provider
    provider.get_server_type.assert_called_once_with("cx22")


@TestScenario
def resolve_provider_returns_validated_type(self):
    validated = MagicMock()
    provider = MagicMock()
    provider.get_server_type.return_value = validated
    _, resolved_st = _resolve_provider("cx22", [provider])
    assert resolved_st is validated


@TestScenario
def resolve_provider_first_match_wins(self):
    p1 = _supporting_provider()
    p2 = _supporting_provider()
    resolved_p, _ = _resolve_provider("cx22", [p1, p2])
    assert resolved_p is p1
    p2.get_server_type.assert_not_called()


@TestScenario
def resolve_provider_skips_raises(self):
    p1 = _non_supporting_provider()
    p2 = _supporting_provider()
    resolved_p, _ = _resolve_provider("cx22", [p1, p2])
    assert resolved_p is p2


@TestScenario
def resolve_provider_no_match_raises(self):
    import pytest
    p1 = _non_supporting_provider()
    p2 = _non_supporting_provider()
    try:
        _resolve_provider("unknown-type", [p1, p2])
        assert False, "expected ServerTypeError"
    except ServerTypeError as e:
        assert "unknown-type" in str(e)


@TestScenario
def resolve_provider_empty_raises(self):
    try:
        _resolve_provider("cx22", [])
        assert False, "expected ServerTypeError"
    except ServerTypeError:
        pass


@TestScenario
def resolve_provider_non_servertype_error_propagates(self):
    """Auth/network errors must not be swallowed as 'type unsupported'."""
    p = MagicMock()
    p.get_server_type.side_effect = ConnectionError("network failure")
    try:
        _resolve_provider("cx22", [p])
        assert False, "expected ConnectionError"
    except ConnectionError:
        pass


# ---------------------------------------------------------------------------
# get_server_disk_size  (disk- minimum root-disk label)
# ---------------------------------------------------------------------------


@TestScenario
def disk_size_absent_returns_none(self):
    assert get_server_disk_size(["self-hosted", "type-cx22"]) is None


@TestScenario
def disk_size_plain_integer(self):
    assert get_server_disk_size(["self-hosted", "disk-100"]) == 100


@TestScenario
def disk_size_gb_suffix(self):
    assert get_server_disk_size(["disk-100GB"]) == 100
    assert get_server_disk_size(["disk-100gb"]) == 100


@TestScenario
def disk_size_multiple_takes_max(self):
    """A minimum: the largest requested disk wins."""
    assert get_server_disk_size(["disk-40", "disk-200", "disk-80"]) == 200


@TestScenario
def disk_size_invalid_ignored(self):
    assert get_server_disk_size(["disk-notanumber"]) is None


@TestScenario
def disk_size_respects_label_prefix(self):
    assert get_server_disk_size(["acme-disk-120"], label_prefix="acme") == 120
    # Without the prefix the bare disk- label does not match.
    assert get_server_disk_size(["disk-120"], label_prefix="acme") is None


# ---------------------------------------------------------------------------
# get_server_provider  (provider- pin label)
# ---------------------------------------------------------------------------


@TestScenario
def provider_label_absent_returns_none(self):
    assert get_server_provider(["self-hosted", "type-cx22"]) is None


@TestScenario
def provider_label_returns_name(self):
    assert get_server_provider(["self-hosted", "provider-aws"]) == "aws"


@TestScenario
def provider_label_last_wins(self):
    assert get_server_provider(["provider-hetzner", "provider-scaleway"]) == "scaleway"


@TestScenario
def provider_label_respects_prefix(self):
    assert get_server_provider(["acme-provider-aws"], label_prefix="acme") == "aws"
    assert get_server_provider(["provider-aws"], label_prefix="acme") is None


# ---------------------------------------------------------------------------
# fixed_root_disk  (provider capability used by the disk- resolution gate)
# ---------------------------------------------------------------------------


@TestScenario
def hetzner_fixed_root_disk_from_type(self):
    """Hetzner reports the server type's bundled disk (hcloud .disk, GB)."""
    provider = HetznerCloudProvider(token="tok")
    st = ProviderServerType(name="cx22", _native=ServerType(name="cx22", disk=80))
    assert provider.fixed_root_disk(st) == 80


@TestScenario
def hetzner_fixed_root_disk_none_when_unknown(self):
    provider = HetznerCloudProvider(token="tok")
    st = ProviderServerType(name="cx22", _native=None)
    assert provider.fixed_root_disk(st) is None


@TestScenario
def base_fixed_root_disk_defaults_none(self):
    """Providers with a resizable/unknown root disk are never gated."""
    p = MagicMock(spec=[])
    from testflows.github.runners.cloud_provider import CloudProvider

    assert CloudProvider.fixed_root_disk(p, MagicMock()) is None


# ---------------------------------------------------------------------------
# _resolve_locations
# ---------------------------------------------------------------------------


def _location_provider(valid):
    """A provider that recognises only *valid* locations (identity expansion)."""
    provider = MagicMock()
    provider.name = "aws"
    provider.default_location = "us-east-1a"
    provider.expand_location_label.side_effect = lambda loc: [loc]

    def _get_location(loc):
        if loc in valid:
            return loc
        raise LocationError(f"{loc} not found")

    provider.get_location.side_effect = _get_location
    return provider


@TestScenario
def resolve_locations_skips_unrecognised(self):
    """A location the provider does not recognise is skipped, not fatal.

    Regression: a Hetzner workflow run against AWS carries leaked `in-hel1...`
    labels; resolution must skip them and place on the labels AWS understands,
    rather than raising and aborting the job (as the recycle loop once did).
    """
    provider = _location_provider(valid={"us-east-1a"})
    resolved = list(
        _resolve_locations(provider, ["hel1-fsn1-nbg1", "nbg1", "us-east-1a"], "job")
    )
    assert resolved == [("us-east-1a", "us-east-1a")], resolved


@TestScenario
def resolve_locations_uses_default_for_none(self):
    """A None candidate (no in- label) resolves through the provider default."""
    provider = _location_provider(valid={"us-east-1a"})
    resolved = list(_resolve_locations(provider, [None], "job"))
    assert resolved == [(None, "us-east-1a")], resolved


@TestScenario
def resolve_locations_all_unrecognised_yields_nothing(self):
    """When no candidate is recognised, nothing is yielded (caller falls through)."""
    provider = _location_provider(valid=set())
    resolved = list(_resolve_locations(provider, ["hel1", "nbg1"], "job"))
    assert resolved == [], resolved


# ---------------------------------------------------------------------------
# Feature entry point
# ---------------------------------------------------------------------------


@TestFeature
@Name("scale_up labels")
def feature(self):
    """Label-parsing helpers in scale_up.py."""
    for scenario in loads(current_module(), Scenario):
        scenario()
