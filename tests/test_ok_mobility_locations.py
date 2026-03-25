import unittest

from app.adapters.ok_mobility import OkMobilityAdapter


class OkMobilityLocationsTest(unittest.IsolatedAsyncioTestCase):
    async def test_get_locations_uses_station_name_and_country_map(self) -> None:
        adapter = OkMobilityAdapter()

        async def fake_soap_request(*args, **kwargs):
            return """<soap:Envelope xmlns:soap=\"http://schemas.xmlsoap.org/soap/envelope/\"><soap:Body><getStationsResultResponse><getStationsResult><RentalStation><StationID>640</StationID><Station>OK AUH - Airport</Station><City>ABU DHABI</City><CountryID>239</CountryID><Latitude>24.45</Latitude><Longitude>54.64</Longitude><StationType>2</StationType></RentalStation></getStationsResult></getStationsResultResponse></soap:Body></soap:Envelope>"""

        adapter._soap_request = fake_soap_request  # type: ignore[method-assign]
        locations = await adapter.get_locations()

        self.assertEqual(len(locations), 1)
        self.assertEqual(locations[0]["name"], "OK AUH - Airport")
        self.assertEqual(locations[0]["provider_location_id"], "640")
        self.assertEqual(locations[0]["country"], "United Arab Emirates")
        self.assertEqual(locations[0]["country_code"], "AE")
        self.assertEqual(locations[0]["location_type"], "airport")


if __name__ == "__main__":
    unittest.main()
