import pytest

from app.properties import format_inr, parse_inr


@pytest.mark.parametrize(
    ("written", "rupees"),
    [
        ("30K", 30_000),
        ("₹30K", 30_000),
        ("30 k", 30_000),
        ("30,000", 30_000),
        ("30000", 30_000),
        ("50 lakh", 5_000_000),
        ("50L", 5_000_000),
        ("50 lac", 5_000_000),
        ("50 lakhs", 5_000_000),
        ("1 crore", 10_000_000),
        ("1.2 Cr", 12_000_000),
        ("1.2cr", 12_000_000),
        ("2.5 crores", 25_000_000),
        ("Rs. 25 lakh", 2_500_000),
        ("INR 5000000", 5_000_000),
        ("rs 45,00,000", 4_500_000),
        ("30k/month", 30_000),
        ("28,000 per month", 28_000),
        ("25000/-", 25_000),
        ("5 million", 5_000_000),
        (".5 cr", 5_000_000),
        ("  1 Crore ", 10_000_000),
        (25_000, 25_000),
        (8_500_000.0, 8_500_000),
    ],
)
def test_reads_amounts_the_way_people_write_them(written, rupees):
    assert parse_inr(written) == rupees


@pytest.mark.parametrize("written", ["abc", "-5", "10 bhk", "", "lakh", "1..2 cr", True, -100])
def test_rejects_anything_that_is_not_an_amount(written):
    with pytest.raises(ValueError):
        parse_inr(written)


@pytest.mark.parametrize(
    ("rupees", "label"),
    [
        (None, None),
        (500, "₹500"),
        (28_000, "₹28k"),
        (28_500, "₹28.5k"),
        (150_000, "₹1.5 L"),
        (8_500_000, "₹85 L"),
        (12_000_000, "₹1.2 Cr"),
        (5_000_000_000, "₹500 Cr"),
    ],
)
def test_formats_in_indian_units(rupees, label):
    assert format_inr(rupees) == label
