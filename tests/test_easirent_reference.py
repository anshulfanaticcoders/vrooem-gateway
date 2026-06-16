from app.adapters.easirent_reference import resolve_terms_metadata


def test_easirent_us_terms_expose_source_backed_policy_data() -> None:
    terms = resolve_terms_metadata("US", "$USA202", 500.0)

    assert terms is not None
    assert terms["deposit_amount"] == 500.0
    assert terms["deposit_currency"] == "USD"
    assert terms["fuel_policy"] == "Full-to-full / like-for-like"
    assert "GPS" in terms["counter_only_extras"]

    conditions = " ".join(
        condition for section in terms["terms"] for condition in section["conditions"]
    )
    assert "debit cards are not accepted for the deposit" in conditions
    assert "not shown as pre-bookable Vrooem extras" in conditions


def test_easirent_us_inclusive_terms_use_supplier_deposit_rule() -> None:
    terms = resolve_terms_metadata("US", "$USA202A", 250.0)

    assert terms is not None
    assert terms["deposit_amount"] == 250.0

    conditions = " ".join(
        condition for section in terms["terms"] for condition in section["conditions"]
    )
    assert "zero excess" in conditions
