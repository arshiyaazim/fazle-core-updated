"""
Shadow-only recruitment conversation layer.

This package is intentionally not imported by the live message router. It provides
read-only helpers for comparing a richer recruitment/general inquiry reply layer
against the current deterministic fazle-core behavior.
"""

from .recruitment import generate_recruitment_reply_shadow, simulate_current_core_reply

__all__ = ["generate_recruitment_reply_shadow", "simulate_current_core_reply"]
