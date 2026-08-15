from features.service import enabled_features


def test_explicit_false_disables_default_enabled_feature():
    assert enabled_features(["new_checkout"], {"new_checkout": False}) == []


def test_missing_override_uses_default():
    assert enabled_features(["new_checkout"], {}) == ["new_checkout"]
