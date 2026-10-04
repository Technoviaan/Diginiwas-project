"""GET /v1/prompts: the guided flows the app shows."""

from app.api.v1.schemas import PromptsResponse


def test_serves_the_four_flows_in_order(api):
    flows = PromptsResponse.model_validate(api.get("/v1/prompts").json()).flows

    assert [flow.id for flow in flows] == ["find_home", "compare_homes", "explore_locality", "price_trends"]
    assert [flow.title for flow in flows] == [
        "Find My Home",
        "Compare Homes",
        "Explore Locality",
        "Price Trends",
    ]
    assert all(flow.opening_question.endswith((".", "?")) and flow.chips for flow in flows)


def test_find_my_home_offers_the_chips_the_app_shows(api):
    [find_home, *_] = api.get("/v1/prompts").json()["flows"]

    assert "What type of property are you looking for?" in find_home["opening_question"]
    assert [group["options"] for group in find_home["chips"]] == [
        ["Buy", "Rent"],
        ["House", "Plot", "Flat", "Commercial"],
        ["Select Location"],
        ["Set Budget"],
    ]
    assert find_home["examples"] == ["3 BHK house in Ambala under ₹70 lakh."]
    assert find_home["empty_message"] is None


def test_compare_homes_carries_the_message_for_nothing_selected(api):
    compare = next(flow for flow in api.get("/v1/prompts").json()["flows"] if flow["id"] == "compare_homes")
    assert compare["empty_message"].startswith("You haven't selected any properties for comparison yet.")


def test_explore_locality_and_price_trends_say_what_an_answer_covers(api):
    flows = {flow["id"]: flow for flow in api.get("/v1/prompts").json()["flows"]}

    assert "Connectivity" in flows["explore_locality"]["answer_includes"]
    assert "Current asking-price range" in flows["price_trends"]["answer_includes"]
    assert flows["price_trends"]["examples"] == ["Show me plot price trends in Sector 9, Ambala."]


def test_the_flows_need_no_model_or_listings(api, model, properties_api):
    api.get("/v1/prompts")
    assert model.requests == [] and properties_api.requests == []
