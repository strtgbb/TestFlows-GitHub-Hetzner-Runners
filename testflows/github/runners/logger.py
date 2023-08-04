# Copyright 2023 Katteli Inc.
# TestFlows.com Open-Source Software Testing Framework (http://testflows.com)
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import sys
import logging

logger = logging.getLogger("testflows.github.runners")


class LoggerAdapter(logging.LoggerAdapter):
    def process(self, msg, kwargs):
        if kwargs.get("extra") is None:
            kwargs["extra"] = self.extra
        else:
            extra = {}
            for k, v in self.extra.items():
                extra[k] = kwargs["extra"].get(k, v)
            kwargs["extra"] = extra
        return msg, kwargs


logger = LoggerAdapter(
    logger,
    {
        "run_id": "",
        "job_id": "",
        "server_name": "",
        "standby_server": "",
        "recyclable_server": "",
        "interval": "",
    },
)

columns = [
    "asctime",
    "levelname",
    "message",
    "interval",
    "run_id",
    "job_id",
    "server_name",
    "threadName",
    "funcName",
]


def default_config(level=logging.INFO):
    """Apply default logging configuration."""
    global logger

    logger.logger.setLevel(level)
    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter(
        fmt=("%(asctime)s " "%(message)s"),
        datefmt="%H:%M:%S",
    )
    handler.setFormatter(formatter)
    logger.logger.addHandler(handler)
