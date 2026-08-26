from app.services.character_role_types import normalize_character_role_type


def test_structured_role_enum_is_preserved() -> None:
    assert normalize_character_role_type("protagonist") == "protagonist"
    assert normalize_character_role_type("mentor") == "mentor"


def test_natural_language_is_not_interpreted_as_a_role() -> None:
    assert normalize_character_role_type("主角，穿越者，陆家三岁孙女") == "other"
    assert normalize_character_role_type("主角的父亲", default=None) is None


def test_internal_merged_alias_sentinel_is_preserved() -> None:
    assert normalize_character_role_type("merged_alias") == "merged_alias"


def test_unknown_structured_value_uses_explicit_default() -> None:
    assert normalize_character_role_type("unknown") == "other"
    assert normalize_character_role_type("unknown", default="supporting") == "supporting"
