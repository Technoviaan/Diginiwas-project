"""The published API contract: Swagger must describe what the API really returns."""

import pytest

from app.api.v1 import openapi as docs
from app.api.v1.schemas import ChatRequest, ChatResponse, ErrorResponse, HistoryResponse, StreamEvent
from app.insights import PropertySnapshot

SNAPSHOT_SCHEMAS = (
    "PropertySnapshot",
    "PriceComparison",
    "ScopeComparison",
    "RentalYield",
    "LocalityTrend",
    "DataConfidence",
    "ConfidenceFactor",
    "CalculationStep",
    "LocalitySource",
)


@pytest.fixture
def spec(api):
    return api.get("/openapi.json").json()


def test_every_documented_example_is_valid():
    for example in docs.CHAT_REQUEST_EXAMPLES.values():
        ChatRequest.model_validate(example["value"])
    for example in docs.CHAT_RESPONSE_EXAMPLES.values():
        value = example["value"]
        assert ChatResponse.model_validate(value).model_dump(mode="json") == value
    HistoryResponse.model_validate(docs.HISTORY_EXAMPLE)
    for example in docs.SSE_EXAMPLES.values():
        for line in example["value"].split("\n\n"):
            if line:
                StreamEvent.model_validate_json(line.removeprefix("data: "))
    for response in [*docs.CHAT_ERRORS.values(), docs.SESSION_NOT_FOUND]:
        ErrorResponse.model_validate(response["content"]["application/json"]["example"])


def test_snapshot_examples_are_exactly_what_the_api_returns():
    # Both were captured from real runs; a model change that alters the
    # response shape breaks this until the examples are regenerated.
    for example in docs.SNAPSHOT_EXAMPLES.values():
        value = example["value"]
        assert PropertySnapshot.model_validate(value).model_dump(mode="json") == value
    for response in (docs.LISTING_NOT_FOUND, docs.LISTINGS_UNAVAILABLE, docs.LISTINGS_TIMEOUT):
        ErrorResponse.model_validate(response["content"]["application/json"]["example"])


def test_snapshot_is_documented_with_both_examples(spec):
    operation = spec["paths"]["/v1/properties/{property_id}/snapshot"]["get"]
    ok = operation["responses"]["200"]
    assert sorted(ok["content"]["application/json"]["examples"]) == ["own_data", "web_quoted"]
    assert ok["content"]["application/json"]["schema"] == {"$ref": "#/components/schemas/PropertySnapshot"}
    assert "X-API-Version" in ok["headers"]
    assert "Card → field" in operation["description"]


def test_every_snapshot_field_is_described(spec):
    schemas = spec["components"]["schemas"]
    undescribed = [
        f"{name}.{field}"
        for name in SNAPSHOT_SCHEMAS
        for field, definition in schemas[name]["properties"].items()
        if "description" not in definition
    ]
    assert undescribed == []


def test_operations_are_stable(spec):
    operation_ids = sorted(op["operationId"] for item in spec["paths"].values() for op in item.values())
    assert operation_ids == [
        "chat_stream_v1_chat_stream_post",
        "chat_v1_chat_post",
        "delete_session_v1_sessions__session_id__delete",
        "health_health_get",
        "history_v1_sessions__session_id__history_get",
        "property_snapshot_v1_properties__property_id__snapshot_get",
        "versions_versions_get",
    ]


def test_stream_is_documented_as_server_sent_events(spec):
    content = spec["paths"]["/v1/chat/stream"]["post"]["responses"]["200"]["content"]
    assert list(content) == ["text/event-stream"]
    assert content["text/event-stream"]["schema"] == {"$ref": "#/components/schemas/StreamEvent"}
    assert sorted(content["text/event-stream"]["examples"]) == ["area_rates", "search"]


def test_chat_documents_a_response_for_each_kind_of_question(spec):
    operation = spec["paths"]["/v1/chat"]["post"]
    examples = operation["responses"]["200"]["content"]["application/json"]["examples"]
    assert sorted(examples) == ["area_rates", "locality_guide", "search", "selected_property"]
    assert all(
        examples[name]["value"]["sources"] for name in ("area_rates", "locality_guide", "selected_property")
    )
    assert [card["id"] for card in examples["selected_property"]["value"]["properties"]] == ["DW-1003"]
    requests = operation["requestBody"]["content"]["application/json"]["examples"]
    assert {"new_chat", "selected_property", "near_a_property", "area_rates", "locality_guide"} <= set(
        requests
    )
    assert "property_id" not in requests["new_chat"]["value"]
    assert requests["selected_property"]["value"]["property_id"] == "DW-1003"
    fields = list(spec["components"]["schemas"]["ChatRequest"]["properties"])
    assert fields == ["message", "session_id", "property_id", "system_prompt"]
    assert (
        '"type": "sources"'
        in spec["paths"]["/v1/chat/stream"]["post"]["responses"]["200"]["content"]["text/event-stream"][
            "examples"
        ]["area_rates"]["value"]
    )


def test_no_schema_reference_has_sibling_keywords(spec):
    def references(node):
        if isinstance(node, dict):
            if "$ref" in node:
                yield node
            for value in node.values():
                yield from references(value)
        elif isinstance(node, list):
            for value in node:
                yield from references(value)

    assert [ref for ref in references(spec["paths"]) if len(ref) > 1] == []


def test_every_card_field_is_described(spec):
    fields = spec["components"]["schemas"]["PropertyCard"]["properties"]
    assert [name for name, field in fields.items() if "description" not in field] == []
