"""Compatibility shim for frozen mainline migrations 0008/0009.

The measurement validator moved to ``aisc_evaluation`` with the model, but the
frozen mainline migrations reference this historical module path. Keep the
path importable without reintroducing a core -> evaluation import.
"""

from .common import validate_flat_json

__all__ = ["validate_flat_json"]
