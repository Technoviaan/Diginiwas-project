"""The listing data this service exposes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from pydantic import BaseModel, Field


class PropertyCard(BaseModel):
    """Everything a frontend needs to render a listing card - and nothing else."""

    id: str = Field(
        ..., description="Listing ID. Use it as the key for favourites and links.", examples=["DW-1003"]
    )
    title: str = Field(..., description="Listing headline, shown as the card title.")
    project_name: str | None = Field(
        None, description="Building or project name.", examples=["Royal Residency"]
    )
    description: str | None = Field(None, description="The seller's description of the property.")
    category: str | None = Field(None, description="Residential, Commercial, Plot/Land, …")
    transaction_type: str | None = Field(
        None, description="`Sale` or `Rent`. Lease listings are reported as `Rent`."
    )

    # `int | float`, not `float`: pydantic would otherwise turn 5 into 5.0,
    # and the card would read "Floor 5.0" or "1650.0 sqft".
    price: int | float | None = Field(None, description="Price in rupees. For rentals, the monthly rent.")
    price_label: str | None = Field(
        None,
        description="Price formatted for display. Show it next to `price_period`.",
        examples=["₹85 L", "₹28k"],
    )
    price_period: str | None = Field(None, description="`/mo` for rentals; `null` for sale listings.")
    price_per_sqft: int | float | None = Field(None, description="Rupees per square foot.")
    maintenance: int | float | None = Field(None, description="Maintenance charge in rupees, as listed.")
    booking_amount: int | float | None = Field(None, description="Booking amount in rupees.")
    negotiable: bool = Field(False, description="Whether the seller marked the price as negotiable.")
    verified: bool = Field(True, description="Verified by DigiNiwas. Drives the VERIFIED badge.")

    city: str | None = Field(None, description="City.")
    locality: str | None = Field(
        None, description="Locality or neighbourhood. Show it with `city` next to the pin icon."
    )
    address: str | None = Field(None, description="Street address.")
    latitude: float | None = Field(None, description="For a map pin.")
    longitude: float | None = Field(None, description="For a map pin.")

    bedrooms: str | None = Field(
        None, description='Bedroom count as a string; can be `7+`. Show it as "<n> BHK".'
    )
    bathrooms: str | None = Field(None, description="Bathroom count as a string; can be `7+`.")
    balconies: str | None = Field(None, description="Balcony count as a string.")
    size: int | float | None = Field(None, description="Super built-up area, in `size_unit`.")
    size_unit: str | None = Field(None, description="Unit for `size` and `carpet_area`, usually `sqft`.")
    carpet_area: int | float | None = Field(None, description="Carpet area, in `size_unit`.")
    furnishing: str | None = Field(None, description="Furnished, Semi-Furnished or Unfurnished.")
    facing: str | None = Field(None, description="Direction the property faces.")
    parking: str | None = Field(None, description="Parking type, e.g. Covered.")
    floor_no: int | float | None = Field(None, description="Floor the property is on.")
    total_floors: int | float | None = Field(None, description="Floors in the building.")

    amenities: list[str] = Field(default_factory=list, description="Amenities listed by the seller.")
    tags: list[str] = Field(default_factory=list, description="Highlights such as `Ready To Move`.")
    image: str | None = Field(
        None, description="Cover photo URL for the card; `null` if the listing has no photos."
    )
    images: list[str] = Field(default_factory=list, description="All photo URLs, cover first.")
    url: str | None = Field(
        None,
        description=(
            "Link for the View Property button. `null` unless the server sets "
            "`PROPERTY_URL_TEMPLATE`; then build the link from `id`."
        ),
    )
    listed_on: date | None = Field(None, description="The day the listing was created.")


@dataclass(frozen=True, slots=True)
class PropertyPage:
    """One page of search results."""

    cards: list[PropertyCard]
    total: int
    page: int
    total_pages: int | None
