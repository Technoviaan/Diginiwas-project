"""GET /v1/properties/{id}/snapshot."""

import httpx

from tests.fakes import make_listing

SUBJECT_ID = "DW-SUB"


def dataset() -> list[dict]:
    subject = make_listing(SUBJECT_ID, price=6_000_000, size=1100)
    peers = [make_listing(f"DW-S{index}", price=7_000_000, size=1100) for index in range(6)]
    # Bigger homes in the same locality: they widen the comparison without
    # belonging to the narrow set.
    peers += [make_listing(f"DW-3B{index}", price=9_000_000, size=1600, bedrooms="3") for index in range(2)]
    rentals = [
        make_listing(f"DW-R{index}", transaction_type="Rent", price=rent, size=1100)
        for index, rent in enumerate([24_000, 25_000, 26_000, 25_500])
    ]
    return [subject, *peers, *rentals]


def test_returns_everything_the_card_needs(api, properties_api):
    properties_api.listings = dataset()
    response = api.get(f"/v1/properties/{SUBJECT_ID}/snapshot")

    assert response.status_code == 200
    body = response.json()
    assert body["property_id"] == SUBJECT_ID
    assert body["price_comparison"]["headline"] == "14% below"
    assert body["price_comparison"]["caption"] == "similar 2 BHK homes within 3 km"
    assert body["price_comparison"]["basis"].startswith("₹5,455 per sqft")
    assert [scope["scope"] for scope in body["price_comparison"]["breakdown"]] == [
        "similar_nearby",
        "locality",
    ]
    assert body["rental_yield"]["available"] is True
    assert body["rental_yield"]["headline"].endswith("%")
    trend = body["locality_trend"]
    assert trend["available"] is False  # no search provider key in tests
    assert trend["headline"] == "Not available yet"
    assert (trend["source"], trend["quote"], trend["yearly_percent"]) == (None, None, None)
    assert "price history" in trend["reason"]
    assert body["data_confidence"]["level"] in {"High", "Medium", "Low"}
    assert [step["title"] for step in body["calculation"]][-1] == "Data confidence"
    assert len(body["calculation"]) == 6
    assert body["radius_km"] == 3.0
    assert body["locality_sources"] == []  # search is not configured in tests
    assert response.headers["X-API-Version"] == "v1"


def test_unknown_listing_is_404(api, properties_api):
    properties_api.listings = dataset()
    response = api.get("/v1/properties/DW-NOPE/snapshot")
    assert (response.status_code, response.json()) == (404, {"detail": "No listing with that ID."})


def test_reports_a_listings_service_outage(make_api, properties_api):
    properties_api.listings = dataset()
    properties_api.error = httpx.ConnectError("down")
    with make_api(raise_server_exceptions=False) as client:
        response = client.get(f"/v1/properties/{SUBJECT_ID}/snapshot")
    assert response.status_code == 502
    assert response.json() == {"detail": "The listings service is unavailable."}


def test_is_documented(api):
    spec = api.get("/openapi.json").json()
    operation = spec["paths"]["/v1/properties/{property_id}/snapshot"]["get"]
    assert operation["summary"] == "Property snapshot"
    assert sorted(operation["responses"]) == ["200", "404", "422", "502", "504"]
    assert "property insights v1" in [tag["name"] for tag in spec["tags"]]
