from features.resolver import resolve_flag
from features.service import enabled_features


def test_explicit_true_overrides_disabled_default():
    assert resolve_flag("recommendations", {"recommendations": True}) is True


def test_explicit_false_for_unknown_feature_is_preserved():
    assert resolve_flag("experimental", {"experimental": False}) is False


def test_mixed_feature_list_uses_each_layer():
    result = enabled_features(
        ["new_checkout", "recommendations", "experimental"],
        {"new_checkout": False, "recommendations": True},
    )
    assert result == ["recommendations"]
