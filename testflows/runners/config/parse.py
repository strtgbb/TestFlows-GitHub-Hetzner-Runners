import yaml
import logging
import logging.config
import re

from .config import (
    Config,
    standby_runner,
    cloud,
    deploy_,
    path,
    image,
    location,
    server_type,
    aws_provider,
    scaleway_provider,
    dedicated_static_provider,
    dedicated_static_group,
    dedicated_static_ssh,
    provider_defaults,
    provider_list,
)

logger = logging.getLogger("testflows.runners")
DEDICATED_STATIC_GROUP_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def parse_config(filename: str):
    """Load and parse yaml configuration file into config object.

    Does not check if ssh_key, or additional_ssh_keys exist.
    Does not check server_type exists.
    Does not check image exists.
    Does not check location exists.
    Does not check server_type is available for the location.
    Does not check if image exists for the server_type.
    """
    with open(filename, "r") as f:
        doc = yaml.load(f, Loader=yaml.SafeLoader)

    if doc.get("config") is None:
        assert False, "config: entry is missing"

    doc = doc["config"]

    if doc.get("setup_script"):
        assert (
            False
        ), "config.setup_script is deprecated, use the new config.scripts option"

    if doc.get("startup_x64_script"):
        assert (
            False
        ), "config.startup_x64_script is deprecated, use the new config.scripts option"

    if doc.get("startup_arm64_script"):
        assert (
            False
        ), "config.startup_x64_script is deprecated, see the new config.scripts option"

    if doc.get("ssh_key") is not None:
        assert isinstance(doc["ssh_key"], str), "config.ssh_key: is not a string"
        doc["ssh_key"] = path(doc["ssh_key"], check_exists=False)

    if doc.get("additional_ssh_keys") is not None:
        assert isinstance(
            doc["additional_ssh_keys"], list
        ), "config.additional_ssh_keys: not a list"
        for i, key in enumerate(doc["additional_ssh_keys"]):
            assert isinstance(
                key, str
            ), f"config.additional_ssh_keys[{i}]: is not a string"

    if doc.get("with_label") is not None:
        assert isinstance(doc["with_label"], list), "config.with_label: is not a list"
        for i, label in enumerate(doc["with_label"]):
            assert isinstance(label, str), f"config.with_label[{i}]: is not a string"
        doc["with_label"] = [label.lower().strip() for label in doc["with_label"]]

    if doc.get("label_prefix") is not None:
        assert isinstance(
            doc["label_prefix"], str
        ), "config.label_prefix: is not a string"
        doc["label_prefix"] = doc["label_prefix"].lower().strip()

    if doc.get("meta_label") is not None:
        assert isinstance(
            doc["meta_label"], dict
        ), "config.meta_label is not a dictionary"
        for i, meta in enumerate(doc["meta_label"]):
            assert isinstance(
                meta, str
            ), f"config.meta_label.{meta}: name is not a string"
            assert isinstance(
                doc["meta_label"][meta], list
            ), f"config.meta_label.{meta}: is not a list"
            for j, v in enumerate(doc["meta_label"][meta]):
                assert isinstance(
                    v, str
                ), f"config.meta_label.{meta}[{j}]: is not a string"
            doc["meta_label"][meta] = set(doc["meta_label"][meta])

        doc["meta_label"] = {
            meta.lower().strip(): [
                label.lower().strip() for label in doc["meta_label"][meta]
            ]
            for meta in doc["meta_label"]
        }

    if doc.get("recycle") is not None:
        assert isinstance(doc["recycle"], bool), "config.recycle: is not a boolean"

    if "recycle_without_rebuild" in doc:
        assert False, (
            "config.recycle_without_rebuild has been removed; use "
            "config.providers.hetzner.recycle_with_rebuild instead "
            "(note the inverse semantics)"
        )

    if doc.get("recycle_grace_period") is not None:
        v = doc["recycle_grace_period"]
        assert isinstance(v, int), "config.recycle_grace_period: is not integer"
        assert v >= 0, "config.recycle_grace_period: must be >= 0"

    if doc.get("end_of_life") is not None:
        v = doc["end_of_life"]
        assert isinstance(v, int), "config.end_of_life: is not integer"
        assert v > 0 and v < 60, "config.end_of_life: is not > 0 and < 60"

    if doc.get("delete_random") is not None:
        assert isinstance(
            doc["delete_random"], bool
        ), "config.delete_random: is not a boolean"

    if doc.get("max_runners") is not None:
        v = doc["max_runners"]
        assert isinstance(v, int) and v > 0, "config.max_runners: is not an integer > 0"

    if doc.get("max_runners_for_label") is not None:
        assert isinstance(
            doc["max_runners_for_label"], list
        ), "config.max_runners_for_label: is not a list"
        for i, item in enumerate(doc["max_runners_for_label"]):
            assert isinstance(
                item, dict
            ), f"config.max_runners_for_label[{i}]: is not an object"
            assert (
                "labels" in item
            ), f"config.max_runners_for_label[{i}]: missing 'labels' field"
            assert (
                "max" in item
            ), f"config.max_runners_for_label[{i}]: missing 'max' field"
            assert isinstance(
                item["labels"], list
            ), f"config.max_runners_for_label[{i}].labels: is not a list"
            assert (
                isinstance(item["max"], int) and item["max"] > 0
            ), f"config.max_runners_for_label[{i}].max: is not an integer > 0"
            for j, label in enumerate(item["labels"]):
                assert isinstance(
                    label, str
                ), f"config.max_runners_for_label[{i}].labels[{j}]: is not a string"
                assert (
                    label.strip()
                ), f"config.max_runners_for_label[{i}].labels[{j}]: cannot be empty"
            # Convert to our internal format (set of labels, count)
            doc["max_runners_for_label"][i] = (
                set(label.strip().lower() for label in item["labels"]),
                item["max"],
            )

    if doc.get("max_runners_in_workflow_run") is not None:
        v = doc["max_runners_in_workflow_run"]
        assert (
            isinstance(v, int) and v > 0
        ), "config.max_runners_in_workflow_run: is not an integer > 0"

    # Hetzner's default image/type/location/volume moved under
    # providers.hetzner.defaults (uniform with aws/scaleway); the top-level
    # keys are gone. Hard-error so an old config fails loudly instead of
    # silently ignoring them.
    for _removed in (
        "default_image",
        "default_server_type",
        "default_location",
        "default_volume_location",
        "default_volume_size",
    ):
        assert doc.get(_removed) is None, (
            f"config.{_removed}: is not supported; use "
            f"config.providers.hetzner.defaults instead"
        )

    if doc.get("workers") is not None:
        v = doc["workers"]
        assert isinstance(v, int) and v > 0, "config.workers: is not an integer > 0"

    if doc.get("scripts") is not None:
        try:
            doc["scripts"] = path(doc["scripts"])
        except Exception as e:
            assert False, f"config.scripts: {e}"

    if doc.get("max_powered_off_time") is not None:
        v = doc["max_powered_off_time"]
        assert (
            isinstance(v, int) and v > 0
        ), "config.max_powered_off_time: is not an integer > 0"

    if doc.get("max_unused_runner_time") is not None:
        v = doc["max_unused_runner_time"]
        assert (
            isinstance(v, int) and v > 0
        ), "config.max_unused_runner_time: is not an integer > 0"

    if doc.get("max_runner_registration_time") is not None:
        v = doc["max_runner_registration_time"]
        assert (
            isinstance(v, int) and v > 0
        ), "config.max_runner_registration_time: is not an integer > 0"

    if doc.get("max_server_ready_time") is not None:
        v = doc["max_server_ready_time"]
        assert (
            isinstance(v, int) and v > 0
        ), "config.max_server_ready_time: is not an integer > 0"

    if doc.get("scale_up_interval") is not None:
        v = doc["scale_up_interval"]
        assert (
            isinstance(v, int) and v > 0
        ), "config.scale_up_interval: is not an integer > 0"

    if doc.get("scale_down_interval") is not None:
        v = doc["scale_down_interval"]
        assert (
            isinstance(v, int) and v > 0
        ), "config.scale_down_interval: is not an integer > 0"

    if doc.get("metrics_port") is not None:
        v = doc["metrics_port"]
        assert (
            isinstance(v, int) and v > 0 and v < 65536
        ), "config.metrics_port: is not an integer between 1 and 65535"

    if doc.get("metrics_host") is not None:
        v = doc["metrics_host"]
        assert isinstance(v, str), "config.metrics_host: is not a string"
        assert v.strip(), "config.metrics_host: cannot be empty"

    if doc.get("dashboard_port") is not None:
        v = doc["dashboard_port"]
        assert (
            isinstance(v, int) and v > 0 and v < 65536
        ), "config.dashboard_port: is not an integer between 1 and 65535"

    if doc.get("dashboard_host") is not None:
        v = doc["dashboard_host"]
        assert isinstance(v, str), "config.dashboard_host: is not a string"
        assert v.strip(), "config.dashboard_host: cannot be empty"

    if doc.get("debug") is not None:
        assert isinstance(doc["debug"], bool), "config.debug: not a boolean"

    if doc.get("logger_config") is not None:
        assert (
            doc["logger_config"].get("loggers") is not None
        ), "config.logger_config.loggers is not defined"
        assert (
            doc["logger_config"]["loggers"].get("testflows.runners") is not None
        ), 'config.logger_config.loggers."testflows.runners" is not defined'
        assert (
            doc["logger_config"]["loggers"]["testflows.runners"].get("handlers")
            is not None
        ), 'config.logger_config.loggers."testflows.runners".handlers is not defined'

        assert isinstance(
            doc["logger_config"]["loggers"]["testflows.runners"]["handlers"],
            list,
        ), 'config.logger_config.loggers."testflows.runners".handlers is not a list'
        assert (
            "stdout" in doc["logger_config"]["loggers"]["testflows.runners"]["handlers"]
        ), 'config.logger_config.loggers."testflows.runners".handlers missing stdout'

        assert (
            doc["logger_config"]["handlers"].get("rotating_logfile") is not None
        ), "config.logger_config.handlers.rotating_logfile is not defined"
        assert (
            doc["logger_config"]["handlers"]["rotating_logfile"].get("filename")
            is not None
        ), "config.logger_config.handlers.rotating_logfile.filename is not defined"

        try:
            logging.config.dictConfig(doc["logger_config"])
        except Exception as e:
            assert False, f"config.logger_config: {e}"

    if doc.get("logger_format") is not None:
        _logger_format_columns = {}
        assert isinstance(
            doc["logger_format"], dict
        ), f"config.logger_format is not a dictionary"

        assert (
            doc["logger_format"].get("delimiter") is not None
        ), "config.logger_format.delimiter is not defined"
        assert isinstance(
            doc["logger_format"]["delimiter"], str
        ), f"config.logger_format.delimiter is not a string"

        assert (
            doc["logger_format"].get("columns") is not None
        ), "config.logger_format.columns  is not defined"
        assert isinstance(
            doc["logger_format"]["columns"], list
        ), "config.logger_format.columns is not a list"

        for i, item in enumerate(doc["logger_format"]["columns"]):
            assert (
                item.get("column") is not None
            ), f"config.logger_format[{i}].column is not defined"
            assert isinstance(
                item["column"], str
            ), f"config.logger_format[{i}].column is not a string"
            assert (
                item.get("index") is not None
            ), f"config.logger_format[{i}].index is not defined"
            assert (
                isinstance(item["index"], int) and item["index"] >= 0
            ), f"config.logger_format[{i}].index: {item['index']} is not an integer >= 0"
            assert (
                item.get("width") is not None
            ), f"config.logger_format[{i}].width is not defined"
            assert (
                isinstance(item["width"], int) and item["width"] >= 0
            ), f"config.logger_format[{i}].width: {item['width']} is not an integer >= 0"
            _logger_format_columns[item["column"]] = (item["index"], item["width"])
        doc["logger_format"]["columns"] = _logger_format_columns

        assert (
            doc["logger_format"].get("default") is not None
        ), "config.logger_format.default is not defined"
        assert isinstance(
            doc["logger_format"]["default"], list
        ), "config.logger_format.default is not an array"

        for i, item in enumerate(doc["logger_format"]["default"]):
            assert (
                item.get("column") is not None
            ), f"config.logger_format.default[{i}].column is not defined"
            assert (
                item["column"] in doc["logger_format"]["columns"]
            ), f"config.logger_format.default[{i}].column is not valid"
            if item.get("width") is not None:
                assert (
                    isinstance(item["width"], int) and item["width"] > 0
                ), f"config.logger_format.default[{i}].width is not an integer > 0"

    if doc.get("cloud") is not None:
        if doc["cloud"].get("server_name") is not None:
            assert isinstance(
                doc["cloud"]["server_name"], str
            ), "config.cloud.server_name: is not a string"

        if doc["cloud"].get("ssh_user") is not None:
            assert isinstance(
                doc["cloud"]["ssh_user"], str
            ), "config.cloud.ssh_user: is not a string"

        cloud_provider = doc["cloud"].get("provider") or "hetzner"
        assert cloud_provider in ("hetzner", "aws", "scaleway"), (
            "config.cloud.provider: must be one of 'hetzner', 'aws', 'scaleway' "
            f"(got {cloud_provider!r}); dedicated_static cannot host the controller"
        )

        raw_deploy = doc["cloud"].get("deploy") or {}
        if cloud_provider == "hetzner":
            # Hetzner deploy specs are hcloud-typed; coerce + keep the cx23/ubuntu
            # defaults from deploy_ when omitted.
            for field, factory in (
                ("server_type", server_type),
                ("image", image),
                ("location", location),
            ):
                if raw_deploy.get(field) is not None:
                    try:
                        raw_deploy[field] = factory(raw_deploy[field])
                    except Exception as e:
                        assert False, f"config.cloud.deploy.{field}: {e}"
            if raw_deploy.get("setup_script") is not None:
                try:
                    raw_deploy["setup_script"] = path(raw_deploy["setup_script"])
                except Exception as e:
                    assert False, f"config.cloud.deploy.setup_script: {e}"
            deploy_obj = deploy_(**raw_deploy)
        else:
            # Non-Hetzner: keep specs as raw provider-native strings (validated at
            # deploy time via the provider's get_image/get_server_type/get_location);
            # do NOT inherit the Hetzner-shaped deploy_ defaults, so unset fields
            # fall back to the provider's own defaults in cloud.deploy.
            for field in ("server_type", "image", "location"):
                if raw_deploy.get(field) is not None:
                    assert isinstance(
                        raw_deploy[field], str
                    ), f"config.cloud.deploy.{field}: is not a string"
            deploy_kwargs = {
                "server_type": raw_deploy.get("server_type"),
                "image": raw_deploy.get("image"),
                "location": raw_deploy.get("location"),
            }
            if raw_deploy.get("setup_script") is not None:
                try:
                    deploy_kwargs["setup_script"] = path(raw_deploy["setup_script"])
                except Exception as e:
                    assert False, f"config.cloud.deploy.setup_script: {e}"
            deploy_obj = deploy_(**deploy_kwargs)

        doc["cloud"] = cloud(
            provider=cloud_provider,
            server_name=doc["cloud"].get("server_name") or cloud().server_name,
            host=doc["cloud"].get("host"),
            ssh_user=doc["cloud"].get("ssh_user"),
            deploy=deploy_obj,
        )

    if doc.get("standby_runners"):
        assert isinstance(
            doc["standby_runners"], list
        ), "config.standby_runners: is not a list"

        for i, entry in enumerate(doc["standby_runners"]):
            assert isinstance(
                entry, dict
            ), f"config.standby_runners[{i}]: is not an dictionary"
            if entry.get("labels") is not None:
                assert isinstance(
                    entry["labels"], list
                ), f"config.standby_runners[{i}].labels: is not a list"
                for j, label in enumerate(entry["labels"]):
                    assert isinstance(
                        label, str
                    ), f"config.standby_runners[{i}].labels[{j}]: {label} is not a string"
                entry["labels"] = [label.lower().strip() for label in entry["labels"]]
            if entry.get("count") is not None:
                v = entry["count"]
                assert (
                    isinstance(v, int) and v > 0
                ), f"config.standby_runners[{i}].count: is not an integer > 0"
            if entry.get("replenish_immediately") is not None:
                assert isinstance(
                    entry["replenish_immediately"], bool
                ), f"config.standby_runners[{i}].replenish_immediately: is not a boolean"

        doc["standby_runners"] = [
            standby_runner(**entry) for entry in doc["standby_runners"]
        ]

    if doc.get("server_prices") is not None:
        assert False, "config.server_prices: should not be defined"

    if doc.get("config_file") is not None:
        assert False, "config.config_file: should not be defined"

    if doc.get("service_mode") is not None:
        assert False, "config.service_mode: should not be defined"

    if doc.get("embedded_mode") is not None:
        assert False, "config.embedded_mode: should not be defined"

    if doc.get("hetzner_token") is not None:
        assert False, (
            "config.hetzner_token: is not supported; "
            "use config.providers.hetzner.token instead"
        )

    if doc.get("providers") is not None:
        _p = doc["providers"]
        assert isinstance(_p, dict), "config.providers: is not a dictionary"

        _hetzner = None
        if _p.get("hetzner") is not None:
            from ..providers.hetzner import config as _hetzner_config
            _hetzner = _hetzner_config.parse_config_section(_p["hetzner"])

        _aws = None
        if _p.get("aws") is not None:
            a = _p["aws"]
            assert isinstance(a, dict), "config.providers.aws: is not a dictionary"
            if a.get("access_key_id") is not None:
                assert isinstance(
                    a["access_key_id"], str
                ), "config.providers.aws.access_key_id: is not a string"
            if a.get("secret_access_key") is not None:
                assert isinstance(
                    a["secret_access_key"], str
                ), "config.providers.aws.secret_access_key: is not a string"
            _subnets_raw = a.get("subnets")
            if _subnets_raw is not None:
                if isinstance(_subnets_raw, str):
                    _subnets_raw = [_subnets_raw]
                assert isinstance(_subnets_raw, list) and all(
                    isinstance(s, str) for s in _subnets_raw
                ), "config.providers.aws.subnets: must be a string or list of strings"
            _aws_kwargs = dict(
                access_key_id=a.get("access_key_id"),
                secret_access_key=a.get("secret_access_key"),
                security_group=a.get("security_group"),
                subnets=_subnets_raw,
                key_name=a.get("key_name"),
                ssh_user=a.get("ssh_user", "ubuntu"),
            )
            if a.get("max_runners") is not None:
                v = a["max_runners"]
                assert isinstance(v, int) and v > 0, (
                    "config.providers.aws.max_runners: must be an integer > 0"
                )
                _aws_kwargs["max_runners"] = v
            if a.get("end_of_life") is not None:
                v = a["end_of_life"]
                assert isinstance(v, int) and 0 < v < 60, (
                    "config.providers.aws.end_of_life: must be an integer > 0 and < 60"
                )
                _aws_kwargs["end_of_life"] = v
            if a.get("recycle") is not None:
                v = a["recycle"]
                assert isinstance(v, bool), (
                    "config.providers.aws.recycle: is not a boolean"
                )
                _aws_kwargs["recycle"] = v
            if a.get("recycle_grace_period") is not None:
                v = a["recycle_grace_period"]
                assert isinstance(v, int) and v >= 0, (
                    "config.providers.aws.recycle_grace_period: must be an integer >= 0"
                )
                _aws_kwargs["recycle_grace_period"] = v
            _aws_defaults_raw = a.get("defaults")
            if _aws_defaults_raw is not None:
                assert isinstance(
                    _aws_defaults_raw, dict
                ), "config.providers.aws.defaults: is not a dictionary"
                base = aws_provider().defaults
                _aws_volume_size = _aws_defaults_raw.get("volume_size", base.volume_size)
                assert isinstance(_aws_volume_size, int) and _aws_volume_size > 0, (
                    "config.providers.aws.defaults.volume_size: must be an integer > 0 (in GB)"
                )
                _aws_kwargs["defaults"] = provider_defaults(
                    image=_aws_defaults_raw.get("image", base.image),
                    server_type=_aws_defaults_raw.get("server_type", base.server_type),
                    location=_aws_defaults_raw.get("location", base.location),
                    volume_size=_aws_volume_size,
                    volume_location=_aws_defaults_raw.get(
                        "volume_location", base.volume_location
                    ),
                    volume_type=_aws_defaults_raw.get("volume_type", base.volume_type),
                )
            _aws = aws_provider(**_aws_kwargs)

        _scaleway = None
        if _p.get("scaleway") is not None:
            s = _p["scaleway"]
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
                assert isinstance(v, int) and v > 0, (
                    "config.providers.scaleway.max_runners: must be an integer > 0"
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
                _scw_volume_size = _scaleway_defaults_raw.get(
                    "volume_size", base.volume_size
                )
                assert isinstance(_scw_volume_size, int) and _scw_volume_size > 0, (
                    "config.providers.scaleway.defaults.volume_size: must be an integer > 0 (in GB)"
                )
                _scaleway_kwargs["defaults"] = provider_defaults(
                    image=_scaleway_defaults_raw.get("image", base.image),
                    server_type=_scw_server_type,
                    location=_scaleway_defaults_raw.get("location", base.location),
                    volume_size=_scw_volume_size,
                )
            _scaleway = scaleway_provider(**_scaleway_kwargs)

        _dedicated_static = None
        if _p.get("dedicated_static") is not None:
            d = _p["dedicated_static"]
            assert isinstance(
                d, dict
            ), "config.providers.dedicated_static: is not a dictionary"

            claim_ttl_minutes = d.get("claim_ttl_minutes", 360)
            assert (
                isinstance(claim_ttl_minutes, int) and claim_ttl_minutes > 0
            ), "config.providers.dedicated_static.claim_ttl_minutes: must be an integer > 0"

            ssh_defaults_raw = d.get("ssh_defaults") or {}
            assert isinstance(
                ssh_defaults_raw, dict
            ), "config.providers.dedicated_static.ssh_defaults: is not a dictionary"

            ssh_defaults = dedicated_static_ssh()
            if ssh_defaults_raw.get("user") is not None:
                assert isinstance(
                    ssh_defaults_raw["user"], str
                ), "config.providers.dedicated_static.ssh_defaults.user: is not a string"
                assert (
                    ssh_defaults_raw["user"].strip()
                ), "config.providers.dedicated_static.ssh_defaults.user: cannot be empty"
                ssh_defaults.user = ssh_defaults_raw["user"].strip()
            if ssh_defaults_raw.get("port") is not None:
                assert (
                    isinstance(ssh_defaults_raw["port"], int)
                    and 1 <= ssh_defaults_raw["port"] <= 65535
                ), "config.providers.dedicated_static.ssh_defaults.port: must be an integer between 1 and 65535"
                ssh_defaults.port = ssh_defaults_raw["port"]
            if ssh_defaults_raw.get("key") is not None:
                assert isinstance(
                    ssh_defaults_raw["key"], str
                ), "config.providers.dedicated_static.ssh_defaults.key: is not a string"
                assert (
                    ssh_defaults_raw["key"].strip()
                ), "config.providers.dedicated_static.ssh_defaults.key: cannot be empty"
                ssh_defaults.key = path(ssh_defaults_raw["key"].strip(), check_exists=False)

            groups_raw = d.get("groups")
            assert isinstance(
                groups_raw, dict
            ), "config.providers.dedicated_static.groups: is not a dictionary"
            assert groups_raw, "config.providers.dedicated_static.groups: cannot be empty"

            groups: dict[str, dedicated_static_group] = {}
            meta = doc.get("meta_label") or {}

            # Type labels carry the configured label_prefix at runtime
            # (get_server_types prepends "<label_prefix>-" before "type-"), so the
            # validation must look for the same prefixed form, not a bare "type-".
            # label_prefix conventionally ends with "-" (like server_name_prefix);
            # tolerate either form, matching get_server_types.
            type_prefix = (doc.get("label_prefix") or "").strip().lower()
            if type_prefix and not type_prefix.endswith("-"):
                type_prefix += "-"
            type_prefix += "type-"

            for group_name, group in groups_raw.items():
                assert isinstance(
                    group_name, str
                ), "config.providers.dedicated_static.groups: group name is not a string"
                assert (
                    group_name.strip()
                ), "config.providers.dedicated_static.groups: group name cannot be empty"
                group_name = group_name.strip().lower()
                assert DEDICATED_STATIC_GROUP_NAME_RE.match(group_name), (
                    "config.providers.dedicated_static.groups: invalid group name "
                    f"'{group_name}' (must match ^[a-z0-9][a-z0-9-]*$)"
                )
                assert isinstance(
                    group, dict
                ), f"config.providers.dedicated_static.groups.{group_name}: is not a dictionary"

                labels = group.get("labels")
                assert isinstance(
                    labels, list
                ), f"config.providers.dedicated_static.groups.{group_name}.labels: is not a list"
                normalized_labels = []
                for i, label in enumerate(labels):
                    assert isinstance(
                        label, str
                    ), f"config.providers.dedicated_static.groups.{group_name}.labels[{i}]: is not a string"
                    label = label.lower().strip()
                    assert (
                        label
                    ), f"config.providers.dedicated_static.groups.{group_name}.labels[{i}]: cannot be empty"
                    normalized_labels.append(label)
                    if label in meta:
                        normalized_labels.extend(
                            [meta_label.lower().strip() for meta_label in meta[label]]
                        )
                normalized_labels = list(dict.fromkeys(normalized_labels))
                assert any(
                    l.startswith(type_prefix) for l in normalized_labels
                ), f"config.providers.dedicated_static.groups.{group_name}.labels: must include at least one '{type_prefix}*' label (or a meta label that expands to one)"
                for label in normalized_labels:
                    if label.startswith(type_prefix):
                        type_name = label.split(type_prefix, 1)[1]
                        assert "-" not in type_name, (
                            f"config.providers.dedicated_static.groups.{group_name}.labels: "
                            f"invalid type label '{label}' (type names with '-' are not supported)"
                        )

                hosts = group.get("hosts")
                assert isinstance(
                    hosts, list
                ), f"config.providers.dedicated_static.groups.{group_name}.hosts: is not a list"
                assert (
                    hosts
                ), f"config.providers.dedicated_static.groups.{group_name}.hosts: cannot be empty"
                normalized_hosts = []
                for i, host in enumerate(hosts):
                    assert isinstance(
                        host, str
                    ), f"config.providers.dedicated_static.groups.{group_name}.hosts[{i}]: is not a string"
                    host = host.strip()
                    assert (
                        host
                    ), f"config.providers.dedicated_static.groups.{group_name}.hosts[{i}]: cannot be empty"
                    normalized_hosts.append(host)

                group_ssh = None
                if group.get("ssh") is not None:
                    raw_ssh = group["ssh"]
                    assert isinstance(
                        raw_ssh, dict
                    ), f"config.providers.dedicated_static.groups.{group_name}.ssh: is not a dictionary"
                    group_ssh = dedicated_static_ssh(
                        user=ssh_defaults.user,
                        port=ssh_defaults.port,
                        key=ssh_defaults.key,
                    )
                    if raw_ssh.get("user") is not None:
                        assert isinstance(
                            raw_ssh["user"], str
                        ), f"config.providers.dedicated_static.groups.{group_name}.ssh.user: is not a string"
                        assert (
                            raw_ssh["user"].strip()
                        ), f"config.providers.dedicated_static.groups.{group_name}.ssh.user: cannot be empty"
                        group_ssh.user = raw_ssh["user"].strip()
                    if raw_ssh.get("port") is not None:
                        assert isinstance(raw_ssh["port"], int) and 1 <= raw_ssh["port"] <= 65535, (
                            f"config.providers.dedicated_static.groups.{group_name}.ssh.port: "
                            "must be an integer between 1 and 65535"
                        )
                        group_ssh.port = raw_ssh["port"]
                    if raw_ssh.get("key") is not None:
                        assert isinstance(
                            raw_ssh["key"], str
                        ), f"config.providers.dedicated_static.groups.{group_name}.ssh.key: is not a string"
                        assert (
                            raw_ssh["key"].strip()
                        ), f"config.providers.dedicated_static.groups.{group_name}.ssh.key: cannot be empty"
                        group_ssh.key = path(raw_ssh["key"].strip(), check_exists=False)

                groups[group_name] = dedicated_static_group(
                    labels=normalized_labels,
                    hosts=normalized_hosts,
                    ssh=group_ssh,
                )

            _dedicated_static = dedicated_static_provider(
                ssh_defaults=ssh_defaults,
                claim_ttl_minutes=claim_ttl_minutes,
                groups=groups,
            )

        _unimplemented = set(_p.keys()) - {
            "hetzner",
            "aws",
            "scaleway",
            "dedicated_static",
        }
        assert not _unimplemented, (
            f"config.providers: {', '.join(sorted(_unimplemented))} "
            f"{'is' if len(_unimplemented) == 1 else 'are'} not yet implemented"
        )

        doc["providers"] = provider_list(
            hetzner=_hetzner,
            aws=_aws,
            scaleway=_scaleway,
            dedicated_static=_dedicated_static,
        )

    try:
        return Config(**doc)
    except Exception as e:
        assert False, f"config: {e}"
