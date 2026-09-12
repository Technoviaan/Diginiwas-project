"""Distance and the statistics the snapshot figures rest on."""

import pytest

from app.insights.geo import distance_km
from app.insights.stats import median, middle_range, without_outliers

INDORE = (22.7533, 75.8937)


def test_distance_between_the_same_point_is_zero():
    assert distance_km(*INDORE, *INDORE) == pytest.approx(0)


def test_a_hundredth_of_a_degree_of_latitude_is_about_1_1_km():
    assert distance_km(22.7533, 75.8937, 22.7633, 75.8937) == pytest.approx(1.11, abs=0.02)


def test_indore_to_bhopal_is_about_170_km():
    assert distance_km(*INDORE, 23.2599, 77.4126) == pytest.approx(170, abs=10)


def test_median_of_nothing_is_nothing():
    assert median([]) is None


def test_one_mistyped_listing_does_not_move_the_median():
    rents = [24_000, 25_000, 25_500, 26_000, 27_000, 2_600_000]  # last one entered yearly
    assert 2_600_000 not in without_outliers(rents)
    assert median(without_outliers(rents)) == pytest.approx(25_500, abs=600)


def test_small_samples_keep_every_value():
    # With three values, quartiles would describe noise rather than the market.
    assert without_outliers([10, 20, 90]) == [10, 20, 90]


def test_middle_range_is_the_middle_half_of_a_real_sample():
    low, high = middle_range([10, 20, 30, 40, 50, 60, 70, 80])
    assert low < 30 and high > 50


def test_middle_range_of_a_small_sample_is_ten_percent_either_side():
    assert middle_range([100, 200, 300]) == pytest.approx((180, 220))


def test_middle_range_of_nothing_is_nothing():
    assert middle_range([]) is None
