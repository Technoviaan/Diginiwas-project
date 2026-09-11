from app.properties import PropertyQuery


def test_only_filters_that_are_set_become_parameters():
    assert PropertyQuery(city="Indore").to_params(limit=6) == {"page": 1, "limit": 6, "city": "Indore"}


def test_parameters_use_the_api_names_and_formats():
    query = PropertyQuery(category="Plot/Land", transaction_type="Rent", max_price=30_000, negotiable=False)
    assert query.to_params(limit=6) == {
        "page": 1,
        "limit": 6,
        "category": "Plot/Land",
        "transactionType": "Rent",
        "maxPrice": 30_000,
        "negotiable": "false",
    }


def test_seven_or_more_rooms_are_sent_as_7_plus():
    # The API matches room counts as exact strings; "7" misses "7+" listings.
    params = PropertyQuery(bedrooms=8, bathrooms=2).to_params(limit=6)
    assert (params["bedrooms"], params["bathrooms"]) == ("7+", "2")
