from collections.abc import Mapping


def resolve_config(
    defaults: Mapping[str, str],
    file_values: Mapping[str, str],
    environment: Mapping[str, str],
) -> dict[str, str]:
    """Merge defaults, a config file, and environment values."""
    resolved = dict(defaults)
    resolved.update(environment)
    resolved.update(file_values)
    return resolved
