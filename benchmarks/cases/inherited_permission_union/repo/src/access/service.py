from access.base import BasePolicy


def is_allowed(policy: BasePolicy, permission: str) -> bool:
    return permission in policy.permissions()
