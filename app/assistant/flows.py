"""The four guided flows the app offers, and how Niwas AI runs each one.

Each flow is one entry point in the DigiNiwas app: an opening question, the
chips shown under it, and a note that tells the model how to conduct that
conversation. The app reads them from `GET /v1/prompts` instead of hardcoding
the wording, and sends `flow` back with each message so the note applies.

The rule every flow shares: ask for one missing detail at a time. A chat
bubble is a bad place for a form.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

FlowId = Literal["find_home", "compare_homes", "explore_locality", "price_trends"]

ONE_AT_A_TIME = (
    "Ask at most ONE short question per reply - never a list of questions, and never for something "
    "the user has already told you. Keep the question to one sentence, in plain text."
)


@dataclass(frozen=True, slots=True)
class ChipGroup:
    """One row of quick chips under the opening question."""

    label: str
    options: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Flow:
    id: FlowId
    title: str
    opening_question: str
    chips: tuple[ChipGroup, ...]
    guidance: str
    examples: tuple[str, ...] = ()
    answer_includes: tuple[str, ...] = ()
    empty_message: str | None = None

    @property
    def note(self) -> str:
        """What the model is told for a turn in this flow."""
        return f"The user is in the {self.title} flow of the DigiNiwas app. {ONE_AT_A_TIME} {self.guidance}"


FIND_HOME = Flow(
    id="find_home",
    title="Find My Home",
    opening_question=(
        "What type of property are you looking for? Tell me your preferred location, budget, and "
        "basic requirements, and I'll find suitable properties for you."
    ),
    chips=(
        ChipGroup(label="Buy or rent", options=("Buy", "Rent")),
        ChipGroup(label="Property type", options=("House", "Plot", "Flat", "Commercial")),
        ChipGroup(label="Location", options=("Select Location",)),
        ChipGroup(label="Budget", options=("Set Budget",)),
    ),
    examples=("3 BHK house in Ambala under ₹70 lakh.",),
    guidance=(
        "A search needs: buy or rent, the property type, a city or locality, and a budget - plus "
        "bedrooms for a home. Ask for whichever is missing, one at a time, in that order. Once you "
        "have buy or rent, the type and a location, search with what you have rather than asking "
        "for more; mention afterwards that you can narrow it by budget or bedrooms."
    ),
)

COMPARE_HOMES = Flow(
    id="compare_homes",
    title="Compare Homes",
    opening_question=(
        "Which properties would you like to compare? Select 2 or more properties, and I'll compare "
        "their price, location, size, amenities, and key differences."
    ),
    chips=(
        ChipGroup(label="Properties", options=("Select Saved Properties",)),
        ChipGroup(label="Compare by", options=("Compare Price", "Compare Location", "Compare Features")),
    ),
    empty_message=(
        "You haven't selected any properties for comparison yet. Search for properties and choose "
        "2 or more options to compare."
    ),
    guidance=(
        "Compare the listings the app named, or the ones shown earlier in this conversation. With "
        "fewer than two, reply exactly: \"You haven't selected any properties for comparison yet. "
        'Search for properties and choose 2 or more options to compare." Otherwise compare them on '
        "price and price per sqft, size, bedrooms, locality, furnishing and amenities, using only "
        "their own listing data, and end with the one difference that matters most for a buyer. The "
        "cards are shown under your reply, so do not restate every field: 4 short sentences at most."
    ),
)

EXPLORE_LOCALITY = Flow(
    id="explore_locality",
    title="Explore Locality",
    opening_question=(
        "Which locality would you like to know more about? I can show you available properties, "
        "connectivity, nearby facilities, and local insights."
    ),
    chips=(
        ChipGroup(label="Locality", options=("Select Locality",)),
        ChipGroup(label="Nearby", options=("Nearby Schools", "Hospitals & Markets", "Properties Available")),
    ),
    examples=("Tell me about Model Town, Ambala.",),
    answer_includes=(
        "Overview",
        "Available properties",
        "Typical property types",
        "Connectivity",
        "Schools, hospitals and markets",
        "Nearby localities",
    ),
    guidance=(
        "Ask for the locality and its city if you don't have both. Then call locality_guide for that "
        "area and search_properties for what DigiNiwas lists there. Cover in this order, in plain "
        "sentences: what the area is like from what the sources say, how many DigiNiwas listings are "
        "available and what types they are, connectivity with the distances the sources give, then "
        "schools and hospitals. Name a nearby locality only if a result does. Up to 6 short "
        "sentences, no markdown. Name the site behind each web figure, and say which of these you "
        "could not verify instead of filling it in."
    ),
)

PRICE_TRENDS = Flow(
    id="price_trends",
    title="Price Trends",
    opening_question=(
        "Which area or property type would you like to explore price trends for? Select a location, "
        "and I'll show you price movements and the current asking-price range based on available "
        "DigiNiwas data."
    ),
    chips=(
        ChipGroup(label="Location", options=("Select Location",)),
        ChipGroup(label="Property type", options=("Plot Prices", "House Prices", "Commercial Prices")),
    ),
    examples=("Show me plot price trends in Sector 9, Ambala.",),
    answer_includes=(
        "Current asking-price range",
        "Average rate per sq ft, and per sq yd for plots",
        "Recent price trend",
        "Available listings",
        "Comparison with nearby localities",
    ),
    guidance=(
        "Ask for the locality and city, then the property type, one question at a time. Then call "
        "lookup_area_rates with kind 'land' for plots and 'flat' for houses or flats, and "
        "search_properties for the listings DigiNiwas has there. Give the asking-price range and the "
        "average rate with the site that publishes it, the rate per sq yd as well when the user asks "
        "about plots, how many DigiNiwas listings are available, and the trend or a nearby-locality "
        "comparison only when a result states one. Say once that published rates are portal asking "
        "prices, not valuations, and name what could not be established. Up to 5 short sentences."
    ),
)

FLOWS: tuple[Flow, ...] = (FIND_HOME, COMPARE_HOMES, EXPLORE_LOCALITY, PRICE_TRENDS)
FLOWS_BY_ID: dict[str, Flow] = {flow.id: flow for flow in FLOWS}


def flow_note(flow: FlowId | str) -> str | None:
    """What to tell the model for a turn in this flow, or None for an unknown one."""
    known = FLOWS_BY_ID.get(flow)
    return known.note if known is not None else None


def comparison_note(property_ids: list[str]) -> str:
    """Tells the model which listings the app put up for comparison."""
    listings = ", ".join(property_ids)
    return (
        f"The user has selected these DigiNiwas listings in the app to compare: {listings}. Look each "
        "one up (search_properties with the ID) and compare those, whatever else the conversation "
        "mentioned. Don't ask which properties they mean."
    )
