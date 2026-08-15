from collections.abc import Mapping

from features.resolver import resolve_flag


def enabled_features(names: list[str], overrides: Mapping[str, bool]) -> list[str]:
    return [name for name in names if resolve_flag(name, overrides)]
