import xml.etree.ElementTree as ET
from datetime import date, time

from app.adapters.yesaway import NS, RATE_PLANS, YesawayAdapter
from app.schemas.booking import BookingExtra, CreateBookingRequest, DriverInfo
from app.schemas.location import ProviderLocationEntry
from app.schemas.search import SearchRequest


def _plan(rate_code: str) -> dict:
    return next(plan for plan in RATE_PLANS if plan["rate_code"] == rate_code)


def _availability(package: str, total: str) -> ET.Element:
    xml = f"""
    <VehAvail
      xmlns="http://www.opentravel.org/OTA/2003/05"
      package_code="{package}"
      Unique_id="{package}_63"
      discount_code=""
    >
      <VehAvailCore Status="Available" IsOnRequest="false">
        <Vehicle
          TransmissionType="Automatic"
          AirConditionInd="true"
          BaggageQuantity="3"
          PassengerQuantity="7"
        >
          <VehType VehicleCategory="3" DoorCount="5"/>
          <VehClass Size="8"/>
          <VehMakeModel VehGroupID="63" Name="Toyota Fortuner" Code="FFAR" ExactModel="true"/>
          <PictureURL>https://imgcdn.example/fortuner.jpg</PictureURL>
        </Vehicle>
        <RentalRate>
          <RateDistance VehiclePeriodUnitName="RentalPeriod" DistUnitName="km" Unlimited="true"/>
          <VehicleCharges>
            <VehicleCharge
              Amount="{total}"
              CurrencyCode="THB"
              TaxInclusive="true"
              Purpose="1"
              Description="Base Rate"
              PaymentType="arrival"
            />
          </VehicleCharges>
          <RateQualifier CorpDiscountNmbr="{package}" RateQualifier="" RatePeriod="Other"/>
        </RentalRate>
        <TotalCharge
          RateTotalAmount="{total}"
          EstimatedTotalAmount="{total}"
          TotalAmount="{total}"
          PrepaidTotalAmount="0"
          PODTotalAmount="{total}"
          CurrencyCode="THB"
          UseDeposit="true"
          Deposit="15000"
        />
        <Fees>
          <Fee
            CurrencyCode="THB"
            IncludedInRate="true"
            IncludedInEstTotalInd="true"
            Amount="300"
            UnitPirce="100"
            LiabilityAmount="15000"
            Description="Collision Damage Waiver"
            Purpose="4"
          />
        </Fees>
      </VehAvailCore>
      <VehAvailInfo>
        <PricedCoverages>
          <PricedCoverage>
            <Coverage CoverageType="7"/>
            <Charge
              Amount="300"
              UnitPrice="100"
              CurrencyCode="THB"
              LiabilityAmount="15000"
              Description="Collision Damage Waiver"
              IncludedInRate="true"
              IncludedInEstTotalInd="true"
            />
          </PricedCoverage>
        </PricedCoverages>
      </VehAvailInfo>
      <VehExtraInfo>
        <SpecialEquipList>
          <SpecialEquip
            EquipType="401"
            Per="Order"
            CurrencyCode="THB"
            Amount="400"
            MaxQuantity="2"
            Description="late night drop-offs charge"
            IsCapped="false"
          />
          <SpecialEquip
            EquipType="7"
            Per="Day"
            CurrencyCode="THB"
            Amount="200"
            MaxQuantity="2"
            Description="Infant Car Seat"
            IsCapped="false"
          />
          <SpecialEquip
            EquipType="8"
            Per="Day"
            CurrencyCode="THB"
            Amount="200"
            MaxQuantity="2"
            Description="Child toddler seat"
            IsCapped="false"
          />
          <SpecialEquip
            EquipType="9"
            Per="Day"
            CurrencyCode="THB"
            Amount="200"
            MaxQuantity="2"
            Description="Booster Seat"
            IsCapped="false"
          />
          <SpecialEquip
            EquipType="301"
            Per="Day"
            CurrencyCode="THB"
            Amount="0"
            MaxQuantity="5"
            Description="Additional Drivers Fee"
            IsCapped="false"
          />
        </SpecialEquipList>
      </VehExtraInfo>
      <DrivingAgeInfo><AllowableAgeRange AllowAgeStart="21" AllowAgeEnd="75"/></DrivingAgeInfo>
    </VehAvail>
    """
    return ET.fromstring(xml)


def _request() -> SearchRequest:
    return SearchRequest(
        unified_location_id=1,
        pickup_date=date(2027, 1, 15),
        pickup_time=time(10, 0),
        dropoff_date=date(2027, 1, 18),
        dropoff_time=time(10, 0),
        currency="THB",
        driver_age=35,
    )


def _pickup() -> ProviderLocationEntry:
    return ProviderLocationEntry(
        provider="yesaway",
        pickup_id="BKK01",
        original_name="Bangkok Suvarnabhumi Airport",
        country_code="TH",
        iata="BKK",
        latitude=13.6923085,
        longitude=100.7507142,
    )


def test_yesaway_groups_rate_codes_as_products_and_preserves_extras() -> None:
    adapter = YesawayAdapter()
    request = _request()
    pickup = _pickup()
    base_raw = _availability("W_TH_ORDER_BASE_ARRIVAL", "9300")
    full_raw = _availability("W_TH_ORDER_COM_ARRIVAL", "10800")

    products = [
        adapter._parse_product(base_raw, _plan("W_TH_ORDER_BASE_ARRIVAL"), request, pickup, None),
        adapter._parse_product(full_raw, _plan("W_TH_ORDER_COM_ARRIVAL"), request, pickup, None),
    ]
    vehicle = adapter._parse_vehicle(
        base_raw,
        _plan("W_TH_ORDER_BASE_ARRIVAL"),
        products,
        request,
        pickup,
        None,
    )

    assert vehicle is not None
    assert vehicle.supplier_id == "yesaway"
    assert vehicle.provider_rate_id == "W_TH_ORDER_BASE_ARRIVAL_63"
    assert vehicle.pricing.total_price == 9300
    assert vehicle.pricing.currency == "THB"
    assert vehicle.pricing.deposit_amount == 15000
    assert vehicle.supplier_data["products"][0]["rate_code"] == "W_TH_ORDER_BASE_ARRIVAL"
    assert vehicle.supplier_data["products"][1]["rate_code"] == "W_TH_ORDER_COM_ARRIVAL"
    assert vehicle.supplier_data["products"][1]["total"] == 10800
    assert vehicle.min_driver_age == 21
    assert vehicle.max_driver_age == 75

    extra_ids = {extra.id for extra in vehicle.extras}
    assert "ext_yesaway_7" in extra_ids
    assert "ext_yesaway_8" in extra_ids
    assert "ext_yesaway_9" in extra_ids
    assert "ext_yesaway_301" in extra_ids
    assert "ext_yesaway_401" not in extra_ids
    assert next(extra for extra in vehicle.extras if extra.id == "ext_yesaway_7").total_price == 600


def test_yesaway_reservation_xml_uses_selected_product_and_extra_quantities() -> None:
    adapter = YesawayAdapter()
    request = _request()
    pickup = _pickup()
    raw = _availability("W_TH_ORDER_COM_ARRIVAL", "10800")
    product = adapter._parse_product(raw, _plan("W_TH_ORDER_COM_ARRIVAL"), request, pickup, None)
    vehicle = adapter._parse_vehicle(
        raw,
        _plan("W_TH_ORDER_BASE_ARRIVAL"),
        [product],
        request,
        pickup,
        None,
    )
    assert vehicle is not None

    booking_request = CreateBookingRequest(
        vehicle_id=vehicle.id,
        search_id="search_yesaway_1",
        package="FULL",
        driver=DriverInfo(
            first_name="Vrooem",
            last_name="Tester",
            email="yesaway.test@example.com",
            phone="+31612345678",
            age=35,
            driving_license_number="TEST-LICENSE-1",
            address="Damrak 1",
            city="Amsterdam",
            country="NL",
            postal_code="1012JS",
        ),
        extras=[BookingExtra(extra_id="ext_yesaway_7", quantity=2)],
        pickup_date=date(2027, 1, 15),
        pickup_time="10:00",
        dropoff_date=date(2027, 1, 18),
        dropoff_time="10:00",
        laravel_booking_number="BKTESTYESAWAY",
    )

    xml = adapter._reservation_request_xml(booking_request, vehicle, product)

    assert 'RateQualifier="W_TH_ORDER_COM_ARRIVAL"' in xml
    assert 'CorpDiscountNmbr="W_TH_ORDER_COM_ARRIVAL"' in xml
    assert 'LocationCode="BKK01"' in xml
    assert 'VehGroupID="63"' in xml
    assert 'Code="FFAR"' in xml
    assert 'DriverLicenseNumber="TEST-LICENSE-1"' in xml
    assert 'SpecialEquipPref EquipType="7" Quantity="2" PayType="arrival"' in xml
    assert 'Voucher SeriesCode="BKTESTYESAWAY"' in xml
    assert "TEST - Vrooem booking BKTESTYESAWAY" in xml


def test_yesaway_location_filter_keeps_rate_enabled_locations_only() -> None:
    adapter = YesawayAdapter()
    bkk = ET.fromstring(
        f"""
        <LocationDetail
          xmlns="{NS[1:-1]}"
          Name="Bangkok Suvarnabhumi Airport"
          AtAirport="true"
          Code="BKK01"
          AirportCode="BKK"
        >
          <Address>
            <AddressLine>Airport</AddressLine>
            <CityName Code="Bangkok"/>
            <CountryName Code="TH"/>
          </Address>
          <Coordinate Latitude="13.69" Longitude="100.75"/>
        </LocationDetail>
        """
    )
    test_location = ET.fromstring(
        f"""
        <LocationDetail
          xmlns="{NS[1:-1]}"
          Name="test01"
          AtAirport="true"
          Code="LOS7499S"
          AirportCode="LAX"
        >
          <Address>
            <AddressLine>Test</AddressLine>
            <CityName Code="Los Angeles"/>
            <CountryName Code="US"/>
          </Address>
          <Coordinate Latitude="33.94" Longitude="-118.40"/>
        </LocationDetail>
        """
    )

    parsed_bkk = adapter._parse_location(bkk)
    parsed_test = adapter._parse_location(test_location)

    assert parsed_bkk is not None
    assert parsed_bkk["provider_location_id"] == "BKK01"
    assert adapter._rate_plans_for_location_data(parsed_bkk)
    assert parsed_test is None
