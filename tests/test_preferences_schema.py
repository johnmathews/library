from library.schemas import (
    DEFAULT_DASHBOARD_FIELDS,
    DashboardField,
    DashboardPreferences,
    resolve_dashboard_preferences,
)


def test_unknown_keys_dropped_and_deduped() -> None:
    prefs = DashboardPreferences(dashboard_fields=["kind", "bogus", "kind", "tags"])
    assert prefs.dashboard_fields == [DashboardField.KIND, DashboardField.TAGS]


def test_non_list_coerces_to_empty() -> None:
    prefs = DashboardPreferences(dashboard_fields="kind")  # type: ignore[arg-type]
    assert prefs.dashboard_fields == []


def test_resolve_absent_key_returns_default() -> None:
    assert resolve_dashboard_preferences({}).dashboard_fields == DEFAULT_DASHBOARD_FIELDS
    assert resolve_dashboard_preferences(None).dashboard_fields == DEFAULT_DASHBOARD_FIELDS


def test_resolve_explicit_empty_stays_empty() -> None:
    assert resolve_dashboard_preferences({"dashboard_fields": []}).dashboard_fields == []


def test_resolve_cleans_stored_garbage() -> None:
    resolved = resolve_dashboard_preferences({"dashboard_fields": ["tags", "nope", 7]})
    assert resolved.dashboard_fields == [DashboardField.TAGS]
