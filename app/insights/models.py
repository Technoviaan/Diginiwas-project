"""What the Property Snapshot card shows."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

ConfidenceLevel = Literal["High", "Medium", "Low"]


Direction = Literal["below", "above", "in line", "unknown"]


class ScopeComparison(BaseModel):
    """The same comparison against one set of listings."""

    scope: str = Field(..., description="Which set.", examples=["similar_nearby", "locality"])
    label: str = Field(..., description="The set in words.", examples=["similar 2 BHK homes within 3 km"])
    median_price_per_sqft: int | float | None = Field(
        None, description="Median price per sqft of the set, outliers removed."
    )
    difference_percent: float | None = Field(
        None, description="This listing against that median; negative means cheaper."
    )
    direction: Direction = Field(..., description="Which side of the median this listing sits on.")
    sample_size: int = Field(..., description="Listings in the set, after outliers are removed.")


class PriceComparison(BaseModel):
    """This listing's price per sqft against comparable listings.

    Taken from the narrowest set of listings that holds enough of them;
    `breakdown` carries every set that was available, narrowest first.
    """

    difference_percent: float | None = Field(
        None, description="Negative means cheaper than the comparables. Null when unknown."
    )
    direction: Direction = Field(..., description="Which side of the median this listing sits on.")
    headline: str = Field(..., description="Ready to display, e.g. '3% below'.", examples=["3% below"])
    caption: str = Field(
        ...,
        description="Small print under the headline: what it was compared with.",
        examples=["similar 2 BHK homes within 3 km"],
    )
    listing_price_per_sqft: int | float | None = Field(None, description="This listing's price per sqft.")
    median_price_per_sqft: int | float | None = Field(
        None, description="Median of the comparables, outliers removed."
    )
    sample_size: int = Field(..., description="Comparable listings the median came from.")
    basis: str = Field(..., description="One line naming what the headline compared against.")
    breakdown: list[ScopeComparison] = Field(
        default_factory=list,
        description="Every comparison that could be made, narrowest first. For the details screen.",
    )
    confidence: ConfidenceLevel | None = Field(
        None, description="How far this comparison can be trusted: how many listings, and how close."
    )


class RentalYield(BaseModel):
    """Estimated rental yield: annual rent as a share of the price.

    `source` says where the rent came from:

    - `listings` - a weighted median of DigiNiwas' own comparable rentals,
      closer and more recently listed ones counting for more.
    - `web` - a locality's average rent **quoted** from a web page, used only
      when there are too few comparable rentals. Usually an average over every
      property type, so it carries `quote`, `source_url`, `rent_scope` and
      `confidence: "Low"`; show those with it.
    """

    available: bool = Field(..., description="False when no rent could be established.")
    headline: str = Field(..., description="Ready to display: the gross yield range.", examples=["4.8%–5.4%"])
    low_percent: float | None = Field(None, description="Gross yield at the lower end of typical rents.")
    high_percent: float | None = Field(None, description="Gross yield at the upper end of typical rents.")
    gross_percent: float | None = Field(None, description="Estimated monthly rent × 12 ÷ price × 100.")
    net_percent: float | None = Field(
        None, description="Gross yield less the expenses described in `assumptions`."
    )
    estimated_monthly_rent: int | None = Field(None, description="The monthly rent the yield is based on.")
    estimated_annual_rent: int | None = Field(None, description="`estimated_monthly_rent` × 12.")
    annual_expenses: int | None = Field(
        None, description="Deducted for net yield: vacancy, and maintenance where listed."
    )
    assumptions: list[str] = Field(
        default_factory=list, description="What net yield assumes. Show these with it."
    )
    sample_size: int = Field(..., description="Comparable rentals used; 0 for a web-quoted rent.")
    rent_scope: str | None = Field(
        None,
        description="What the rent describes.",
        examples=["similar 3 BHK rentals within 3 km", "all property types in Vijay Nagar"],
    )
    basis: str = Field(..., description="How it was worked out, in one line.")
    source: Literal["listings", "web"] | None = Field(None, description="Where the rent came from.")
    source_name: str | None = Field(None, description="Publisher of a web-quoted rent.")
    source_url: str | None = Field(None, description="The page a web-quoted rent came from.")
    quote: str | None = Field(None, description="The sentence a web-quoted rent was taken from.")
    confidence: ConfidenceLevel | None = Field(
        None, description="How far the rent can be trusted. Always Low for a web-quoted rent."
    )


class LocalityTrend(BaseModel):
    """How prices in the locality have moved.

    `source` says where the figure came from, and they are not equivalent:

    - `listings` - measured from recorded price history. Not available yet;
      nothing records it.
    - `web` - a figure **quoted** from a page found by search. Someone else's
      claim, not a measurement: it always carries `confidence: "Low"`, the
      `quote` it was taken from and the `source_url` it appeared on. Show
      those next to it, and never give it the same weight as the computed
      figures on this card.
    """

    available: bool = Field(False, description="False when no figure could be established.")
    yearly_percent: float | None = Field(
        None, description="Compounded from `total_percent` over `years`, not taken from the source."
    )
    total_percent: float | None = Field(
        None, description="The whole change the source stated, e.g. 44.2 over 5 years."
    )
    years: int | None = Field(None, description="How many years `total_percent` covers.")
    headline: str = Field(..., description="Ready to display.", examples=["Not available yet"])
    caption: str | None = Field(
        None, description="Small print under the headline.", examples=["Past 3 years"]
    )
    reason: str | None = Field(None, description="Why it is unavailable, when it is.")
    source: Literal["listings", "web"] | None = Field(
        None, description="Where the figure came from. See the note above."
    )
    source_name: str | None = Field(None, description="Publisher, e.g. 'timesofindia.com'.")
    source_url: str | None = Field(None, description="The page the figure was quoted from.")
    quote: str | None = Field(
        None, description="The sentence the figure was taken from, for the reader to judge."
    )
    period: str | None = Field(None, description="The period the figure covers, as the source states it.")
    confidence: ConfidenceLevel | None = Field(None, description="Always 'Low' for a web-quoted figure.")


class ConfidenceFactor(BaseModel):
    """One figure's part in the card's data confidence."""

    figure: Literal["price_comparison", "rental_yield", "locality_trend"] = Field(
        ..., description="Which figure on the card."
    )
    level: ConfidenceLevel = Field(..., description="That figure's own confidence.")
    weight: float = Field(..., description="Its share of the overall score.", examples=[0.5])
    reason: str = Field(..., description="Why it has this level, in one line.")


class DataConfidence(BaseModel):
    """How far the figures on the card can be trusted, taken together.

    Each figure has its own level (see `factors`). Scored High 3, Medium 2 and
    Low 1, they are averaged with weights - price comparison 0.5, rental yield
    0.3, locality trend 0.2 - into `score`: 2.5 or more is High, 1.75 or more
    Medium, anything lower Low. A figure that couldn't be established counts as
    Low. For a rental listing, which has no yield, the weights of the other two
    are used alone. A listing without coordinates is never rated High.
    """

    level: ConfidenceLevel = Field(..., description="Low, Medium or High.")
    headline: str = Field(..., description="Ready to display.", examples=["Medium"])
    caption: str = Field(..., description="Small print.", examples=["24 comparable listings"])
    comparable_listings: int = Field(
        ..., description="Listings behind the price comparison plus rentals behind the yield."
    )
    score: float = Field(..., description="Weighted score from 1 (all Low) to 3 (all High).")
    factors: list[ConfidenceFactor] = Field(
        default_factory=list, description="Each figure's level and why. For the details screen."
    )
    reasons: list[str] = Field(default_factory=list, description="What raised or lowered the level.")


class CalculationStep(BaseModel):
    """One line behind 'See How This Was Calculated'."""

    title: str = Field(..., description="Heading for this step.", examples=["Rental yield"])
    detail: str = Field(..., description="The working, in plain language.")


class LocalitySource(BaseModel):
    """A web page about this locality, found by web search."""

    title: str = Field(..., description="The page's title.")
    url: str = Field(..., description="Link to the page.")
    snippet: str | None = Field(
        None,
        description=(
            "Text from the page. In a chat response, the exact words the reply's figures or places "
            "were taken from; in a snapshot, the search result's excerpt."
        ),
    )
    source: str | None = Field(None, description="Display domain, e.g. 'timesofindia.com'.")


class PropertySnapshot(BaseModel):
    """Everything the Niwas AI Property Snapshot card needs."""

    property_id: str = Field(..., description="The listing's ID.", examples=["DW-1003"])
    title: str = Field(..., description="The listing's headline.")
    locality: str | None = Field(None, description="Locality or neighbourhood.")
    city: str | None = Field(None, description="City.")
    price: int | float | None = Field(None, description="Price in rupees. For rentals, the monthly rent.")
    price_label: str | None = Field(None, description="Price formatted for display.", examples=["₹85 L"])
    transaction_type: str | None = Field(None, description="`Sale` or `Rent`.")

    price_comparison: PriceComparison = Field(..., description="The Price Comparison tile.")
    rental_yield: RentalYield = Field(..., description="The Estimated Rental Yield tile.")
    locality_trend: LocalityTrend = Field(..., description="The Locality Trend tile.")
    data_confidence: DataConfidence = Field(..., description="The Data Confidence tile.")

    calculation: list[CalculationStep] = Field(
        default_factory=list, description="Shown behind 'See How This Was Calculated'."
    )
    locality_sources: list[LocalitySource] = Field(
        default_factory=list,
        description="Web pages about the locality. Empty when search is not configured.",
    )
    radius_km: float = Field(..., description="How far out comparables were taken from.")
    updated_on: date = Field(..., description="The day these figures were computed.")
    disclaimer: str = Field(..., description="Show at the bottom of the card.")
