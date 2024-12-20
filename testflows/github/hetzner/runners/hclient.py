from hcloud import Client

from . import __version__ as project_version, __name__ as project_name


class HClient(Client):

    def __init__(self, *args, **kwargs):
        super().__init__(
            *args,
            application_name=project_name,
            application_version=project_version,
            **kwargs,
        )
