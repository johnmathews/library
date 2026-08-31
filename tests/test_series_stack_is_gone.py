"""The legacy series stack is unmounted.

Asserted against the OpenAPI path set, not by requesting a 404: a 404 can
mean "auth redirected", "trailing slash", or "you typed a path that never
existed", all of which pass while the router is still mounted.
"""

from library.app import create_app


def test_no_legacy_series_or_charts_route_is_mounted() -> None:
    paths = set(create_app().openapi()["paths"])

    legacy = sorted(
        p
        for p in paths
        if p.startswith("/api/charts") or p.startswith("/api/series") or p.endswith("/series")
    )
    assert legacy == [], f"legacy routes still mounted: {legacy}"


def test_the_spending_routes_are_still_mounted() -> None:
    """The guard above must not pass by the whole app failing to build."""
    paths = set(create_app().openapi()["paths"])

    assert "/api/spending" in paths
    assert any(p.startswith("/api/spending/") for p in paths)
