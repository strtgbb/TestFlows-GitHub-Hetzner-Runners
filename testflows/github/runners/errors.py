"""
Exception classes for testflows runners.
"""


class LocationError(Exception):
    pass


class ImageError(Exception):
    pass


class ImageSpecFormatError(ImageError):
    """Raised when an image spec string is not in a format this provider understands.

    get_server_image catches this to skip specs intended for a different provider,
    allowing multiple image- labels to coexist in a multi-cloud job.
    """

    pass


class SetupScriptError(Exception):
    pass


class RecycleScriptError(Exception):
    pass


class StartupScriptError(Exception):
    pass


class ServerTypeError(Exception):
    pass


class ConfigError(Exception):
    pass
