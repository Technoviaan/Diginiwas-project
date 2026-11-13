"""The listings API's second shape: facts nested, and filters that don't reach them.

Newer listings keep location, price and area in nested objects. The API's own
filters don't read them, so a search for a city it does hold comes back empty.
Both halves are handled here: the mapper reads either shape, and the client
checks an empty result against listings fetched unfiltered.
"""

import pytest

from app.properties import PropertiesClient, PropertyQuery
from app.properties.mapper import to_card
from tests.fakes import FakePropertiesAPI, make_listing

pytestmark = pytest.mark.anyio

NESTED = {
    "propertyId": "DWRS-4347216248",
    "title": "RESIDENTIAL-SELL-23-SEP",
    "category": "Sell",
    "buildingType": "Residential",
    "transactionType": "Sale",
    "propertyVerificationStatus": "Verified",
    "location": {
        "city": "Jaipur",
        "state": "Rajasthan",
        "locality": "Ramganj",
        "society": "Mirza Heights",
        "address": "Mirza Ismail Road, Ramganj, Jaipur",
    },
    "price": {"amount": 5000000, "pricePerSqFt": 1000, "negotiable": True},
    "areaDetails": [
        {"value": 1000, "type": "Built-up Area", "unit": "Sq. Yard"},
        {"value": 850, "type": "Carpet Area", "unit": "Sq. Yard"},
    ],
    "media": {"images": [{"url": "https://cdn.test/one.jpg"}, {"url": "https://cdn.test/two.jpg"}]},
    "specifications": {"bathrooms": 2, "coveredParking": 1, "openParking": 1},
    "bedrooms": "3 BHK",
    "balconies": 1,
    "parking": "2999909090909090909090",
    "furnishing": "Semi-Furnished",
    "amenities": ["Gymnasium", "Party Hall"],
    "propertyHighlights": ["Corner Unit"],
    "latitude": 26.916254,
    "longitude": 75.818539,
    "createdAt": "2026-09-23T06:04:32.166Z",
}

NESTED_RENTAL = {
    "propertyId": "DWR-6048326770",
    "title": "RESIDENTIAL-RENT-SEP",
    "buildingType": "Residential",
    "transactionType": "Rent",
    "propertyVerificationStatus": "Verified",
    "location": {"city": "Kolkata", "locality": "College Street"},
    "rent": {"amount": 24000, "frequency": "Per Month", "negotiable": True},
    "securityDeposit": {"amount": 50000},
    "areaDetails": [{"value": 1470, "type": "Carpet Area", "unit": "Sq. Ft."}],
    "bedrooms": "1 BHK",
}


# --- the mapper reads either shape --------------------------------------------


def test_reads_location_price_area_and_photos_from_the_nested_shape():
    card = to_card(NESTED)

    assert (card.city, card.locality) == ("Jaipur", "Ramganj")
    assert card.address == "Mirza Ismail Road, Ramganj, Jaipur"
    assert (card.price, card.price_label, card.price_per_sqft) == (5_000_000, "₹50 L", 1000)
    assert (card.size, card.size_unit, card.carpet_area) == (1000, "sq yd", 850)
    assert card.images == ["https://cdn.test/one.jpg", "https://cdn.test/two.jpg"]
    assert card.image == "https://cdn.test/one.jpg"
    assert card.negotiable is True
    assert card.project_name == "Mirza Heights"


def test_reads_counts_as_numbers_and_ignores_junk_parking():
    card = to_card(NESTED)

    # "3 BHK" is a label; the card holds the count the app renders as "3 BHK".
    assert (card.bedrooms, card.bathrooms, card.balconies) == ("3", "2", "1")
    # The raw `parking` field holds a 22-digit number, so the counts are used.
    assert card.parking == "1 covered, 1 open"


def test_the_property_kind_comes_from_building_type_not_the_listing_type():
    # `category` says "Sell" on these records, which is how it is sold, not what it is.
    assert to_card(NESTED).category == "Residential"


def test_a_rental_takes_its_monthly_amount_from_the_rent_block():
    card = to_card(NESTED_RENTAL)

    assert (card.price, card.price_label, card.price_period) == (24_000, "₹24k", "/mo")
    assert card.booking_amount == 50_000
    assert (card.size, card.size_unit) == (1470, "sqft")


def test_the_flat_shape_still_maps():
    card = to_card(make_listing("DW-1003", locality="Vijay Nagar", city="Indore"))
    assert (card.city, card.locality, card.price) == ("Indore", "Vijay Nagar", 6_000_000)


# --- the client rescues a search the API can't answer --------------------------


def client_for(listings: list[dict]) -> tuple[PropertiesClient, FakePropertiesAPI]:
    api = FakePropertiesAPI(listings)
    return PropertiesClient(base_url="https://properties.test", timeout=5, transport=api.transport), api


async def test_a_city_search_the_api_answers_with_nothing_still_finds_the_listing():
    # The fake filters the way the real API does: on the flat fields only.
    client, api = client_for([NESTED])

    page = await client.search(PropertyQuery(city="Jaipur"), limit=6)

    assert [card.id for card in page.cards] == ["DWRS-4347216248"]
    assert page.total == 1
    # One filtered call that found nothing, then one unfiltered re-read.
    assert len(api.requests) == 2
    assert "city" not in api.last_params


async def test_the_second_look_applies_every_filter():
    cheap = dict(NESTED, propertyId="DWRS-CHEAP", price={"amount": 2_000_000})
    rental = dict(NESTED_RENTAL, propertyId="DWR-OTHER")
    client, _ = client_for([NESTED, cheap, rental])

    in_budget = await client.search(PropertyQuery(city="Jaipur", max_price=3_000_000), limit=6)
    by_rooms = await client.search(PropertyQuery(search="Ramganj", bedrooms=3), limit=6)
    to_rent = await client.search(PropertyQuery(city="Kolkata", transaction_type="Rent"), limit=6)

    assert [card.id for card in in_budget.cards] == ["DWRS-CHEAP"]
    assert [card.id for card in by_rooms.cards] == ["DWRS-4347216248", "DWRS-CHEAP"]
    assert [card.id for card in to_rent.cards] == ["DWR-OTHER"]


async def test_a_search_that_genuinely_matches_nothing_stays_empty():
    client, _ = client_for([NESTED])
    page = await client.search(PropertyQuery(city="Ambala"), limit=6)
    assert page.cards == [] and page.total == 0


async def test_results_from_the_api_are_used_as_they_are():
    client, api = client_for([make_listing("DW-1003", city="Indore")])

    page = await client.search(PropertyQuery(city="Indore"), limit=6)

    assert [card.id for card in page.cards] == ["DW-1003"]
    assert len(api.requests) == 1  # no second call when the API answered


async def test_an_unfiltered_search_is_never_retried():
    client, api = client_for([])
    assert (await client.search(PropertyQuery(), limit=6)).cards == []
    assert len(api.requests) == 1
