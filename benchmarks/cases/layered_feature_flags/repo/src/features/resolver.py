from collections.abc import Mapping

from features.defaults import DEFAULT_FLAGS


def resolve_flag(name: str, overrides: Mapping[str, bool]) -> bool:
    """Resolve an explicit override before consulting application defaults."""
    return overrides.get(name) or DEFAULT_FLAGS.get(name, False)
