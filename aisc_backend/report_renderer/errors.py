"""Error references and the renderer's own exceptions."""
from __future__ import annotations

import logging
import secrets

logger = logging.getLogger("report_renderer")


def new_error_ref() -> str:
    """Eight random hex characters, shown to the user and written to the log with the traceback."""
    return secrets.token_hex(4)


class PlatformUnavailable(Exception):
    """The platform database cannot be reached: the whole render fails."""


class SourceUnavailable(Exception):
    """A source database cannot be reached: only the blocks reading it fail."""

    def __init__(self, source: str):
        self.source = source
        super().__init__(f"the {source} database is unreachable")


def log_block_error(ref: str, instance_id: str, block_type: str) -> None:
    logger.error("block %s (%s) failed, ref %s", instance_id, block_type, ref, exc_info=True)
