import logging
import json
import sys
from typing import Dict, Any


class MultiFormatAdapter(logging.LoggerAdapter):
    def __init__(self, logger: logging.Logger, static_fields: Dict[str, Any] = None):
        super().__init__(logger, {})
        self.static_fields = static_fields or {}

    def process(self, msg, kwargs):
        extra_fields = kwargs.pop("extra", {})
        merged = {**self.static_fields, **extra_fields}

        if merged:
            # there is atleast 1 extra field, log as json
            log_obj = {
                **merged,
                "level": self.logger.level,
                "logger": self.logger.name,
                "message": str(msg),
            }
            return json.dumps(log_obj), kwargs
        else:
            # log as plain text
            return str(msg), kwargs

    def get_level(self):
        return self.logger.level


def get_logger(name, level_str: str = logging.getLevelName(logging.INFO), static_fields: Dict[str, Any] = None) -> MultiFormatAdapter:
    level = getattr(logging, level_str.upper(), logging.INFO)
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)

        formatter = logging.Formatter(
            "%(asctime)s: [%(levelname)-8s]: %(message)s",
            "%Y-%m-%dT%H:%M:%S%z"
        )

        handler.setFormatter(formatter)

        logger.addHandler(handler)
        logger.setLevel(level)

    return MultiFormatAdapter(logger, static_fields)