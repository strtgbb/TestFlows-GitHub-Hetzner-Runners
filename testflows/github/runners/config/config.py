import os
import re
import yaml

from .. import errors
from ..providers.hetzner import config as hetzner_config

# Validators re-exported from the argtypes leaf under their historical names
# (config.parse still does `from .config import path`).
from ..argtypes import (
    path_type as path,
    count_type as count,
    image_type as image,
    location_type as location,
    server_type,
    end_of_life_type as end_of_life,
    meta_label_type,
)

# Schema dataclasses re-exported from the config_schema leaf for backward
# compat. Providers import them from config_schema directly (never this module)
# to avoid the config <-> providers import cycle.
from ..config_schema import (
    standby_runner,
    provider_defaults,
    hetzner_provider,
    aws_provider,
    scaleway_provider,
    dedicated_static_ssh,
    dedicated_static_group,
    dedicated_static_provider,
    provider_list,
    deploy_,
    cloud,
    Config,
)

current_dir = os.path.dirname(__file__)

# add support for parsing ${ENV_VAR} in config
env_pattern = re.compile(r".*?\${(.*?)}.*?")

default_user_config = os.path.expanduser("~/.tfs-runners/config.yaml")

# store all the environment variables used inside the config file
config_vars = {}


def env_constructor(loader, node):
    value = loader.construct_scalar(node)
    for group in env_pattern.findall(value):
        env_value = os.environ.get(group)
        if env_value is None:
            assert (
                False
            ), f"environment variable ${group} used in the config is not defined"
        value = value.replace(f"${{{group}}}", env_value)
        config_vars[group] = env_value
    return value


yaml.add_implicit_resolver("!path", env_pattern, None, yaml.SafeLoader)
yaml.add_constructor("!path", env_constructor, yaml.SafeLoader)


# Re-export error classes from errors module for backwards compatibility
ConfigError = errors.ConfigError
LocationError = errors.LocationError
ImageError = errors.ImageError
SetupScriptError = errors.SetupScriptError
RecycleScriptError = errors.RecycleScriptError
StartupScriptError = errors.StartupScriptError
ServerTypeError = errors.ServerTypeError


def apply_args(config, args):
    """Apply command-line argument overrides onto a Config."""
    for attr in vars(config):
        if attr in [
            "config_file",
            "logger_config",
            "logger_format",
            "cloud",
            "standby_runners",
            "additional_ssh_keys",
            "server_prices",
            "providers",
        ]:
            continue

        arg_value = getattr(args, attr, None)

        if arg_value is not None:
            setattr(config, attr, arg_value)

    # Provider configuration is nested and intentionally skipped above.
    # Apply Hetzner-specific CLI overrides through its provider update hook.
    if config.providers.hetzner is not None:
        hetzner_config.update_from_args(config.providers.hetzner, args)
    elif getattr(args, "hetzner_token", None):
        config.providers.hetzner = hetzner_provider()
        hetzner_config.update_from_args(config.providers.hetzner, args)

    if getattr(args, "cloud_server_name", None) is not None:
        config.cloud.server_name = args.cloud_server_name

    if getattr(args, "cloud_host", None) is not None:
        config.cloud.host = args.cloud_host

    if getattr(args, "cloud_user", None) is not None:
        config.cloud.ssh_user = args.cloud_user

    if getattr(args, "cloud_deploy_location", None) is not None:
        config.cloud.deploy.location = args.cloud_deploy_location

    if getattr(args, "cloud_deploy_server_type", None) is not None:
        config.cloud.deploy.server_type = args.cloud_deploy_server_type

    if getattr(args, "cloud_deploy_image", None) is not None:
        config.cloud.deploy.image = args.cloud_deploy_image

    if getattr(args, "cloud_deploy_setup_script", None) is not None:
        config.cloud.deploy.setup_script = args.cloud_deploy_setup_script


def read(path: str):
    """Load raw configuration document."""
    with open(path, "r") as f:
        return yaml.load(f, Loader=yaml.SafeLoader)


def write(file, doc: dict):
    """Write raw configuration document to file."""
    yaml.dump(doc, file)


def check_setup_script(script: str):
    """Check if setup script is valid."""
    if not os.path.exists(script):
        raise errors.SetupScriptError(f"invalid setup script path '{script}'")
    return script


def check_startup_script(script: str):
    """Check if startup script is valid."""
    if not os.path.exists(script):
        raise errors.StartupScriptError(f"invalid startup script path '{script}'")
    return script


def check_recycle_script(script: str):
    """Check if recycle script is valid."""
    if not os.path.exists(script):
        raise errors.RecycleScriptError(f"invalid recycle script path '{script}'")
    return script
