"""Raw listing JSON to `PropertyCard`, through a whitelist.

The API's raw listings include seller and partner names, emails and phone
numbers, internal review notes and status history. Only the fields named
here are copied, so none of that can reach the model or a client.
"""

from __future__ import annotations

from typing import Any

from app.properties.models import PropertyCard
from app.properties.money import format_inr


def to_card(raw: dict[str, Any], *, url_template: str | None = None) -> PropertyCard:
    """Build a card from one raw listing. Raises ValueError if it has no ID or title."""
    listing_id = _text(raw.get("propertyId")) or _text(raw.get("_id"))
    title = _text(raw.get("title")) or _text(raw.get("projectName"))
    if not listing_id or not title:
        raise ValueError("listing has no id or title")

    transaction = _text(raw.get("transactionType"))
    is_rental = transaction in ("Rent", "Lease")
    price = _number(raw.get("price"))
    images = [
        url
        for image in raw.get("images") or []
        if isinstance(image, dict) and (url := _text(image.get("url")))
    ]

    return PropertyCard(
        id=listing_id,
        title=title,
        project_name=_text(raw.get("projectName")),
        description=_text(raw.get("description")),
        category=_text(raw.get("category")),
        transaction_type="Rent" if is_rental else transaction,
        price=price,
        price_label=format_inr(price),
        price_period="/mo" if is_rental else None,
        price_per_sqft=_number(raw.get("pricePerSqft")),
        maintenance=_number(raw.get("maintenance")),
        booking_amount=_number(raw.get("bookingAmount")),
        negotiable=bool(raw.get("negotiable")),
        verified=raw.get("propertyVerificationStatus") == "Verified",
        city=_text(raw.get("city")),
        locality=_text(raw.get("locality")),
        address=_text(raw.get("address")),
        latitude=_number(raw.get("latitude")),
        longitude=_number(raw.get("longitude")),
        bedrooms=_text(raw.get("bedrooms")),
        bathrooms=_text(raw.get("bathrooms")),
        balconies=_text(raw.get("balconies")),
        size=_number(raw.get("propertySize")) or _number(raw.get("superBuiltupArea")),
        size_unit=_text(raw.get("sizeUnit")) or "sqft",
        carpet_area=_number(raw.get("carpetArea")),
        furnishing=_text(raw.get("furnishing")),
        facing=_text(raw.get("facing")),
        parking=_text(raw.get("parking")),
        floor_no=_number(raw.get("floorNo")),
        total_floors=_number(raw.get("totalFloors")),
        amenities=_strings(raw.get("amenities")),
        tags=_strings(raw.get("tags")),
        image=images[0] if images else None,
        images=images,
        url=url_template.format(id=listing_id) if url_template else None,
    )


def _text(value: Any) -> str | None:
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def _number(value: Any) -> int | float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return int(number) if number.is_integer() else number


def _strings(values: Any) -> list[str]:
    return [value for value in values or [] if isinstance(value, str) and value.strip()]
