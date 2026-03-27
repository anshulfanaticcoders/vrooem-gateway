from app.schemas.pricing import Pricing
from app.schemas.vehicle import Vehicle


def test_vehicle_defaults_do_not_invent_specs():
    """Vehicle created with only required fields should not claim specs it doesn't have."""
    v = Vehicle(
        id="gw_test",
        supplier_id="test_supplier",
        supplier_vehicle_id="sv_1",
        name="Test Car",
        pricing=Pricing(total_price=100.0, daily_rate=33.33),
    )
    assert v.seats is None
    assert v.doors is None
    assert v.bags_large is None
    assert v.bags_small is None
    assert v.transmission is None
    assert v.fuel_type is None
    assert v.air_conditioning is None
    assert v.mileage_policy is None
    assert v.provider_product_id is None
    assert v.provider_rate_id is None
    assert v.availability_status is None
