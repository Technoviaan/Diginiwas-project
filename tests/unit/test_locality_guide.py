"""The locality guide: places web pages list for an area, and what code refuses.

The snippets are taken from real search results for Vijay Nagar and Rau, Indore.
"""

import json

import httpx
import pytest

from app.insights import LocalityGuideFinder, LocalitySearch
from app.insights.guide import _km
from tests.fakes import Reply, ScriptedChatModel

pytestmark = pytest.mark.anyio

EDUSTOKE_TITLE = "33 Best Schools in Vijay Nagar, Indore 2026-2027"
EDUSTOKE_QUOTE = (
    "Best Schools in Vijay Nagar, Indore · SICA Senior Secondary School · PODAR INTERNATIONAL SCHOOL - Indore"
)
EDUSTOKE = f"{EDUSTOKE_QUOTE} · SRI SATHYA SAI VIDYA VIHAR · AJMERA ..."
ROME2RIO_TITLE = "Indore Airport (IDR) to 54 Vijay Nagar Main Road"
ROME2RIO = (
    "The distance between Indore Airport (IDR) and 54 Vijay Nagar Main Road is 13 miles. "
    "The road distance is 7.4 miles."
)


def result(
    title: str, snippet: str, link: str = "https://www.edustoke.com/vn", host: str = "www.edustoke.com"
) -> dict:
    return {"title": title, "snippet": snippet, "link": link, "displayLink": host}


def searching(*items: dict) -> LocalitySearch:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"items": list(items)})

    return LocalitySearch(api_key="key", engine_id="cx", limit=6, transport=httpx.MockTransport(handle))


def claiming(*places: dict) -> ScriptedChatModel:
    return ScriptedChatModel(script=[Reply(json.dumps({"places": list(places)}))])


def place(name: str, quote: str, source: int = 0, *, distance: str | None = None) -> dict:
    return {"name": name, "distance": distance, "quote": quote, "source": source}


async def find(search, model, topic, *, locality="Vijay Nagar", city="Indore"):
    found = await LocalityGuideFinder(search, model, enabled=True).find(locality, city, [topic])
    return found[topic]


# --- accepted ----------------------------------------------------------------


async def test_lists_the_schools_a_directory_names():
    model = claiming(
        place("SICA Senior Secondary School", EDUSTOKE_QUOTE),
        place("PODAR INTERNATIONAL SCHOOL", EDUSTOKE_QUOTE),
    )
    found = await find(searching(result(EDUSTOKE_TITLE, EDUSTOKE)), model, "schools")

    assert [item.name for item in found] == ["SICA Senior Secondary School", "PODAR INTERNATIONAL SCHOOL"]
    assert {(item.distance_km, item.source_name) for item in found} == {(None, "www.edustoke.com")}


async def test_reads_a_road_distance_in_miles_as_km():
    model = claiming(place("Indore Airport (IDR)", ROME2RIO, distance="7.4 miles"))
    search = searching(
        result(ROME2RIO_TITLE, ROME2RIO, link="https://www.rome2rio.com/a", host="www.rome2rio.com")
    )
    [airport] = await find(search, model, "connectivity")
    assert (airport.name, airport.distance_km) == ("Indore Airport (IDR)", 11.9)


async def test_reads_a_distance_in_metres():
    quote = "Rau Railway Station (Rau Halt) is approximately 700 metres away"
    search = searching(
        result("Rau Circle, Indore: Location, Direction & Connectivity", f"A: {quote} on the line.")
    )
    model = claiming(place("Rau Railway Station (Rau Halt)", quote, distance="700 metres"))
    [station] = await find(search, model, "connectivity", locality="Rau")
    assert station.distance_km == 0.7


async def test_matches_the_area_however_it_is_spaced():
    snippet = "Medanta Super Speciality Hospital, Plot No. 8, Rasoma Square, Vijaynagar, AB Road, Indore"
    model = claiming(place("Medanta Super Speciality Hospital", snippet))
    [hospital] = await find(searching(result("MEDANTA HOSPITAL INDORE", snippet)), model, "hospitals")
    assert hospital.name == "Medanta Super Speciality Hospital"


async def test_keeps_at_most_five_places_and_no_repeats():
    names = [f"School Number {n}" for n in range(7)]
    snippet = "Schools in Vijay Nagar, Indore · " + " · ".join(names)
    model = claiming(place(names[0], snippet), *(place(name, snippet) for name in names))
    found = await find(searching(result("Schools in Vijay Nagar", snippet)), model, "schools")
    assert [item.name for item in found] == names[:5]


# --- refused -----------------------------------------------------------------


async def test_refuses_a_driving_or_coaching_class():
    snippet = "Top Schools in Rau, Indore · Sdps International School · Sai Kripa Car Driving School"
    model = claiming(
        place("Sdps International School", snippet), place("Sai Kripa Car Driving School", snippet)
    )
    found = await find(searching(result("Schools in Rau", snippet)), model, "schools", locality="Rau")
    assert [item.name for item in found] == ["Sdps International School"]


async def test_refuses_a_name_that_is_not_in_the_quote():
    model = claiming(place("Delhi Public School", EDUSTOKE_QUOTE))
    assert await find(searching(result(EDUSTOKE_TITLE, EDUSTOKE)), model, "schools") == []


async def test_refuses_a_name_that_is_not_what_was_asked():
    snippet = "52 Hospitals in Vijay Nagar, Indore · Apollo Hospitals · Motherhood Fertility and IVF - Indore"
    model = claiming(place("Apollo Hospitals", snippet), place("Motherhood Fertility and IVF", snippet))
    found = await find(searching(result("52 Hospitals in Vijay Nagar, Indore", snippet)), model, "hospitals")
    assert [item.name for item in found] == ["Apollo Hospitals"]


async def test_refuses_places_listed_for_another_city():
    snippet = "Best Schools in Vijay Nagar, Jaipur · St. Xavier's School"
    model = claiming(place("St. Xavier's School", snippet))
    assert await find(searching(result("Schools in Vijay Nagar, Jaipur", snippet)), model, "schools") == []


async def test_quotes_the_snippet_when_the_model_stitches_its_own_quote():
    model = claiming(place("SICA Senior Secondary School", "SICA Senior Secondary School is in Vijay Nagar"))
    [found] = await find(searching(result(EDUSTOKE_TITLE, EDUSTOKE)), model, "schools")
    assert found.quote == EDUSTOKE


async def test_refuses_a_name_and_quote_the_page_does_not_have():
    model = claiming(place("Delhi Public School Vijay Nagar", "Delhi Public School Vijay Nagar, Indore"))
    assert await find(searching(result(EDUSTOKE_TITLE, EDUSTOKE)), model, "schools") == []


async def test_a_distance_always_needs_a_verbatim_quote():
    model = claiming(
        place("Indore Airport (IDR)", "Indore Airport (IDR) is 7.4 miles away", distance="7.4 miles")
    )
    assert await find(searching(result(ROME2RIO_TITLE, ROME2RIO)), model, "connectivity") == []


async def test_drops_a_station_without_a_distance():
    model = claiming(place("Indore Airport (IDR)", ROME2RIO))
    assert await find(searching(result(ROME2RIO_TITLE, ROME2RIO)), model, "connectivity") == []


async def test_refuses_a_distance_that_is_not_in_the_quote():
    model = claiming(place("Indore Airport (IDR)", ROME2RIO, distance="5 km"))
    assert await find(searching(result(ROME2RIO_TITLE, ROME2RIO)), model, "connectivity") == []


async def test_refuses_a_vague_distance():
    snippet = "The airport Away From the City Approximately 10 to 15 Km, the grate thing"
    model = claiming(place("The airport", snippet, distance="10 to 15 Km"))
    search = searching(result("International Airport in Vijay Nagar, Indore", snippet))
    assert await find(search, model, "connectivity") == []


async def test_never_reads_social_media():
    post = result(
        "[HELP] Public Transport: Airport to Vijay Nagar : r/Indore",
        "Indore Airport to Vijay Nagar is 2 km",
        link="https://www.reddit.com/r/Indore/1",
        host="www.reddit.com",
    )
    model = claiming(place("Indore Airport", "Indore Airport to Vijay Nagar is 2 km", distance="2 km"))
    assert await find(searching(post), model, "connectivity") == []
    assert model.requests == []


@pytest.mark.parametrize(
    ("text", "km"),
    [("10 Km", 10.0), ("7.4 miles", 11.9), ("700 metres", 0.7), ("about 2 kms", 2.0), ("10 to 15 Km", None)],
)
def test_reads_distances(text, km):
    assert _km(text) == km


# --- switched off and cached -------------------------------------------------


async def test_is_off_unless_switched_on_and_configured():
    model = claiming()
    assert await LocalityGuideFinder(searching(), model, enabled=False).find("Rau", "Indore") is None
    unconfigured = LocalitySearch(api_key=None, engine_id=None)
    assert await LocalityGuideFinder(unconfigured, model, enabled=True).find("Rau", "Indore") is None
    assert model.requests == []


async def test_asks_once_per_area_and_topic():
    model = claiming(place("SICA Senior Secondary School", EDUSTOKE_QUOTE))
    finder = LocalityGuideFinder(searching(result(EDUSTOKE_TITLE, EDUSTOKE)), model, enabled=True)

    first = await finder.find("Vijay Nagar", "Indore", ["schools"])
    second = await finder.find("Vijay Nagar", "Indore", ["schools", "schools"])

    assert first == second
    assert len(model.requests) == 1
