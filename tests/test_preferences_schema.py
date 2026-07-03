from library.schemas import (
    DEFAULT_BACKGROUND_TONE,
    DEFAULT_DASHBOARD_FIELDS,
    AppearancePreferences,
    BackgroundTone,
    DashboardField,
    DashboardPreferences,
    KindColorsPreferences,
    resolve_dashboard_preferences,
    resolve_preferences,
)


def test_unknown_keys_dropped_and_deduped() -> None:
    prefs = DashboardPreferences(dashboard_fields=["kind", "bogus", "kind", "tags"])
    assert prefs.dashboard_fields == [DashboardField.KIND, DashboardField.TAGS]


def test_non_list_coerces_to_empty() -> None:
    prefs = DashboardPreferences(dashboard_fields="kind")  # type: ignore[arg-type]
    assert prefs.dashboard_fields == []


def test_order_is_preserved_not_canonicalised() -> None:
    # Order is meaningful (drives the card render order), so a list given out of
    # catalog order round-trips as-is rather than being re-sorted.
    prefs = DashboardPreferences(dashboard_fields=["tags", "kind", "date"])
    assert prefs.dashboard_fields == [DashboardField.TAGS, DashboardField.KIND, DashboardField.DATE]


def test_resolve_absent_key_returns_default() -> None:
    assert resolve_dashboard_preferences({}).dashboard_fields == DEFAULT_DASHBOARD_FIELDS
    assert resolve_dashboard_preferences(None).dashboard_fields == DEFAULT_DASHBOARD_FIELDS


def test_resolve_explicit_empty_stays_empty() -> None:
    assert resolve_dashboard_preferences({"dashboard_fields": []}).dashboard_fields == []


def test_resolve_cleans_stored_garbage() -> None:
    resolved = resolve_dashboard_preferences({"dashboard_fields": ["tags", "nope", 7]})
    assert resolved.dashboard_fields == [DashboardField.TAGS]


def test_appearance_accepts_known_tone() -> None:
    assert AppearancePreferences(background_tone="slate").background_tone == BackgroundTone.SLATE


def test_appearance_coerces_unknown_tone_to_default() -> None:
    prefs = AppearancePreferences(background_tone="chartreuse")  # type: ignore[arg-type]
    assert prefs.background_tone == DEFAULT_BACKGROUND_TONE


def test_resolve_preferences_defaults_tone_when_absent() -> None:
    resolved = resolve_preferences({})
    assert resolved.background_tone == DEFAULT_BACKGROUND_TONE
    assert resolved.dashboard_fields == DEFAULT_DASHBOARD_FIELDS
    assert resolve_preferences(None).background_tone == DEFAULT_BACKGROUND_TONE


def test_resolve_preferences_reads_stored_tone() -> None:
    assert resolve_preferences({"background_tone": "mist"}).background_tone == BackgroundTone.MIST


def test_resolve_preferences_defaults_garbage_tone() -> None:
    assert resolve_preferences({"background_tone": 7}).background_tone == DEFAULT_BACKGROUND_TONE


def test_kind_colors_keeps_valid_hex_lowercased() -> None:
    prefs = KindColorsPreferences(kind_colors={"invoice": "#56B1F3", "receipt": "#34bd68"})
    assert prefs.kind_colors == {"invoice": "#56b1f3", "receipt": "#34bd68"}


def test_kind_colors_drops_invalid_entries() -> None:
    prefs = KindColorsPreferences(
        kind_colors={
            "invoice": "#56b1f3",  # kept
            "bad": "blue",  # not hex
            "short": "#fff",  # 3-digit form not accepted
            "": "#123456",  # empty slug
            "num": 123,  # non-str value
        }  # type: ignore[dict-item]
    )
    assert prefs.kind_colors == {"invoice": "#56b1f3"}


def test_kind_colors_non_dict_coerces_empty() -> None:
    prefs = KindColorsPreferences(kind_colors="nope")  # type: ignore[arg-type]
    assert prefs.kind_colors == {}


def test_resolve_preferences_defaults_kind_colors_empty() -> None:
    assert resolve_preferences({}).kind_colors == {}
    assert resolve_preferences(None).kind_colors == {}


def test_resolve_preferences_cleans_stored_kind_colors() -> None:
    resolved = resolve_preferences({"kind_colors": {"invoice": "#AABBCC", "x": "nope"}})
    assert resolved.kind_colors == {"invoice": "#aabbcc"}
