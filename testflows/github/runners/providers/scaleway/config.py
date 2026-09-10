"""Scaleway provider configuration."""

from ...config_schema import scaleway_provider, provider_defaults


def parse_config_section(section: dict) -> "scaleway_provider":
    """Validate and coerce a ``providers.scaleway`` config section into a
    ``scaleway_provider`` dataclass.
    """
    s = section
    assert isinstance(
        s, dict
    ), "config.providers.scaleway: is not a dictionary"
    for _str_field in (
        "access_key",
        "secret_key",
        "project_id",
        "organization_id",
    ):
        if s.get(_str_field) is not None:
            assert isinstance(
                s[_str_field], str
            ), f"config.providers.scaleway.{_str_field}: is not a string"
    _scaleway_kwargs = dict(
        access_key=s.get("access_key"),
        secret_key=s.get("secret_key"),
        project_id=s.get("project_id"),
        organization_id=s.get("organization_id"),
        ssh_user=s.get("ssh_user", "root"),
    )
    if s.get("max_runners") is not None:
        v = s["max_runners"]
        assert isinstance(v, int) and v >= 0, (
            "config.providers.scaleway.max_runners: must be an integer >= 0"
        )
        _scaleway_kwargs["max_runners"] = v
    if s.get("end_of_life") is not None:
        v = s["end_of_life"]
        assert isinstance(v, int) and 0 < v < 60, (
            "config.providers.scaleway.end_of_life: must be an integer > 0 and < 60"
        )
        _scaleway_kwargs["end_of_life"] = v
    if s.get("recycle") is not None:
        v = s["recycle"]
        assert isinstance(v, bool), (
            "config.providers.scaleway.recycle: is not a boolean"
        )
        _scaleway_kwargs["recycle"] = v
    if s.get("recycle_grace_period") is not None:
        v = s["recycle_grace_period"]
        assert isinstance(v, int) and v >= 0, (
            "config.providers.scaleway.recycle_grace_period: must be an integer >= 0"
        )
        _scaleway_kwargs["recycle_grace_period"] = v
    _scaleway_defaults_raw = s.get("defaults")
    if _scaleway_defaults_raw is not None:
        assert isinstance(
            _scaleway_defaults_raw, dict
        ), "config.providers.scaleway.defaults: is not a dictionary"
        base = scaleway_provider().defaults
        _scw_server_type = _scaleway_defaults_raw.get(
            "server_type", base.server_type
        )
        if _scw_server_type is not None:
            assert "-" not in _scw_server_type, (
                "config.providers.scaleway.defaults.server_type: use the "
                f"dot-form (e.g. '{str(_scw_server_type).replace('-', '.')}') "
                "not the dash-form; the runner label grammar reserves '-'"
            )
        _scw_disk_size = _scaleway_defaults_raw.get(
            "disk_size", base.disk_size
        )
        assert isinstance(_scw_disk_size, int) and _scw_disk_size > 0, (
            "config.providers.scaleway.defaults.disk_size: must be an integer > 0 (in GB)"
        )
        _scaleway_kwargs["defaults"] = provider_defaults(
            image=_scaleway_defaults_raw.get("image", base.image),
            server_type=_scw_server_type,
            location=_scaleway_defaults_raw.get("location", base.location),
            disk_size=_scw_disk_size,
        )
    return scaleway_provider(**_scaleway_kwargs)
