import json

import pytest

from app.properties.mapper import to_card


def test_maps_listing_fields_onto_the_card(listings):
    card = to_card(listings[0])
    assert card.id == "DW-1003"
    assert card.project_name == "Royal Residency"
    assert (card.price, card.price_label, card.price_period) == (8_500_000, "₹85 L", None)
    assert card.image == "https://img.test/dw-1003-1.jpg"
    assert card.verified


def test_seller_partner_and_review_details_never_reach_a_card(listings):
    cards = json.dumps([to_card(listing).model_dump() for listing in listings[:3]])
    for private in (
        "seller@example.com",
        "9000000001",
        "partner@example.com",
        "PRT-TEST",
        "owner2@example.com",
        "Internal review note",
        "Internal status remark",
    ):
        assert private not in cards


def test_whole_numbers_stay_integers_and_blanks_become_null(listings):
    card = to_card(listings[1])
    assert card.floor_no == 3 and isinstance(card.floor_no, int)
    assert card.size == 1100 and isinstance(card.size, int)
    assert card.total_floors is None
    assert (card.image, card.images) == (None, [])
    assert card.amenities == ["Lift"]


def test_lease_listings_are_rentals_priced_per_month(listings):
    card = to_card(listings[2])
    assert (card.transaction_type, card.price_label, card.price_period) == ("Rent", "₹1.5 L", "/mo")
    assert card.bedrooms == "7+"


def test_rejects_a_listing_without_a_title(listings):
    with pytest.raises(ValueError):
        to_card(listings[3])


def test_view_property_link_follows_the_template(listings):
    card = to_card(listings[0], url_template="https://diginiwas.com/property/{id}")
    assert card.url == "https://diginiwas.com/property/DW-1003"
