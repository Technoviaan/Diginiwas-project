"""Property insights: how a listing's price, rent and locality compare.

Price comparison and rental yield are computed from DigiNiwas' own listings
wherever the data allows. A figure that must come from the web instead - a
locality trend, or an average rent when comparable rentals are too few - is
quoted, never estimated, and always carries its source and "Low" confidence.
A figure that can't be established is reported as unavailable, not guessed.
"""

from app.insights.comparables import Comparable, ComparablesFinder
from app.insights.models import PropertySnapshot
from app.insights.rates import AreaRate, AreaRateFinder
from app.insights.rent import RentEstimate, WebRentEstimator
from app.insights.snapshot import SnapshotService
from app.insights.trend import TrendEstimate, WebTrendEstimator
from app.insights.websearch import LocalitySearch

__all__ = [
    "AreaRate",
    "AreaRateFinder",
    "Comparable",
    "ComparablesFinder",
    "LocalitySearch",
    "PropertySnapshot",
    "RentEstimate",
    "SnapshotService",
    "TrendEstimate",
    "WebRentEstimator",
    "WebTrendEstimator",
]
