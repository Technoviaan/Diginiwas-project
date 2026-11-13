"""Raw listing JSON to `PropertyCard`, through a whitelist.

The API's raw listings include seller and partner names, emails and phone
numbers, internal review notes and status history. Only the fields named
here are copied, so none of that can reach the model or a client.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from app.properties.models import PropertyCard
from app.properties.money import format_inr


def to_card(raw: dict[str, Any], *, url_template: str | None = None) -> PropertyCard:
    """Build a card from one raw listing. Raises ValueError if it has no ID or title.

    The API serves two shapes. Older listings are flat (`city`, `price`,
    `propertySize`); newer ones nest the same facts (`location.city`,
    `price.amount`, `areaDetails[]`, `media.images[]`). Both are read here, so
    a listing in either shape is searchable and shows a complete card.
    """
    listing_id = _text(raw.get("propertyId")) or _text(raw.get("_id"))
    title = _text(raw.get("title")) or _text(raw.get("projectName"))
    if not listing_id or not title:
        raise ValueError("listing has no id or title")

    location = _mapping(raw.get("location"))
    specifications = _mapping(raw.get("specifications"))

    transaction = _text(raw.get("transactionType"))
    is_rental = transaction in ("Rent", "Lease")
    # Flat listings put the amount in `price`; nested ones in `price.amount`,
    # and a rental's monthly amount in `rent.amount`.
    price_block = _mapping(raw.get("rent")) if is_rental else _mapping(raw.get("price"))
    price = _number(raw.get("price")) or _number(price_block.get("amount"))
    built_up, carpet = _areas(raw.get("areaDetails"))
    images = _images(raw)

    return PropertyCard(
        id=listing_id,
        title=title,
        project_name=_text(raw.get("projectName")) or _text(location.get("society")),
        description=_text(raw.get("description")) or _text(raw.get("oneLineDescription")),
        # `category` is the listing type on newer records ("Sell"), so the
        # property's own kind comes from buildingType first.
        category=_text(raw.get("buildingType")) or _text(raw.get("category")),
        transaction_type="Rent" if is_rental else transaction,
        price=price,
        price_label=format_inr(price),
        price_period="/mo" if is_rental else None,
        price_per_sqft=_number(raw.get("pricePerSqft")) or _number(price_block.get("pricePerSqFt")),
        maintenance=_number(raw.get("maintenance")),
        booking_amount=_number(raw.get("bookingAmount"))
        or _number(_mapping(raw.get("securityDeposit")).get("amount"))
        or _number(raw.get("securityDeposit")),
        negotiable=bool(raw.get("negotiable") or price_block.get("negotiable")),
        verified=raw.get("propertyVerificationStatus") == "Verified",
        city=_text(raw.get("city")) or _text(location.get("city")),
        locality=_text(raw.get("locality")) or _text(location.get("locality")),
        address=_text(raw.get("address")) or _text(location.get("address")),
        latitude=_number(raw.get("latitude")),
        longitude=_number(raw.get("longitude")),
        bedrooms=_rooms(raw.get("bedrooms") or raw.get("bhk")),
        bathrooms=_rooms(raw.get("bathrooms") or specifications.get("bathrooms")),
        balconies=_rooms(raw.get("balconies")),
        size=_number(raw.get("propertySize")) or _number(raw.get("superBuiltupArea")) or built_up[0],
        size_unit=_text(raw.get("sizeUnit")) or built_up[1] or "sqft",
        carpet_area=_number(raw.get("carpetArea")) or carpet,
        furnishing=_text(raw.get("furnishing")),
        facing=_text(raw.get("facing")) or _text(specifications.get("facing")),
        parking=_parking(raw, specifications),
        floor_no=_number(raw.get("floorNo")) or _number(specifications.get("floorNumber")),
        total_floors=_number(raw.get("totalFloors")) or _number(specifications.get("totalFloors")),
        amenities=_strings(raw.get("amenities")),
        tags=_strings(raw.get("tags")) + _strings(raw.get("propertyHighlights")),
        image=images[0] if images else None,
        images=images,
        url=url_template.format(id=listing_id) if url_template else None,
        listed_on=_date(raw.get("createdAt")),
    )


def _images(raw: dict[str, Any]) -> list[str]:
    """Photo URLs, from the flat `images` list or the newer `media.images`."""
    listed = raw.get("images") or _mapping(raw.get("media")).get("images") or []
    urls: list[str] = []
    for image in listed:
        url = _text(image.get("url")) if isinstance(image, dict) else _text(image)
        if url:
            urls.append(url)
    return urls


def _areas(details: Any) -> tuple[tuple[int | float | None, str | None], int | float | None]:
    """((size, unit), carpet area) from `areaDetails`, preferring built-up for the size."""
    entries = [_mapping(entry) for entry in details or []]
    sized = [(entry, _number(entry.get("value"))) for entry in entries]
    carpet = next(
        (value for entry, value in sized if value and "carpet" in str(entry.get("type", "")).casefold()),
        None,
    )
    for wanted in ("super built-up", "built-up", "plot", "saleable"):
        for entry, value in sized:
            if value and wanted in str(entry.get("type", "")).casefold():
                return (value, _unit(entry.get("unit"))), carpet
    first = next(((entry, value) for entry, value in sized if value), None)
    return ((first[1], _unit(first[0].get("unit"))) if first else (None, None)), carpet


def _unit(value: Any) -> str | None:
    """'Sq. Yard' -> 'sq yd', 'Sq. Ft.' -> 'sqft'."""
    text = _text(value)
    if not text:
        return None
    flat = text.casefold().replace(".", "").replace("-", " ")
    if "yard" in flat or "yd" in flat:
        return "sq yd"
    if "met" in flat or flat.endswith(" m") or "sqm" in flat:
        return "sq m"
    if "acre" in flat:
        return "acre"
    if "bigha" in flat:
        return "bigha"
    return "sqft"


def _rooms(value: Any) -> str | None:
    """'3 BHK' -> '3', 2 -> '2'. The card shows counts, not labels."""
    text = _text(value)
    if text is None:
        return None
    first = text.split()[0] if text.split() else text
    return first if first.rstrip("+").replace(".", "").isdigit() else text


def _parking(raw: dict[str, Any], specifications: dict[str, Any]) -> str | None:
    """Parking as words. Newer listings carry counts, and sometimes junk in `parking`."""
    covered = _number(specifications.get("coveredParking")) or 0
    open_air = _number(specifications.get("openParking")) or 0
    parts = []
    if covered:
        parts.append(f"{covered:g} covered")
    if open_air:
        parts.append(f"{open_air:g} open")
    if parts:
        return ", ".join(parts)
    text = _text(raw.get("parking"))
    # A bare number longer than a parking count is data entry noise, not a value.
    return None if text and text.isdigit() and len(text) > 3 else text


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


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


def _date(value: Any) -> date | None:
    """The day part of an ISO timestamp such as '2026-08-26T09:12:09.260Z'."""
    text = _text(value)
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _strings(values: Any) -> list[str]:
    return [value for value in values or [] if isinstance(value, str) and value.strip()]
