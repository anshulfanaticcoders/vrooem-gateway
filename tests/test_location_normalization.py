import unittest

from app.services.location_normalization import (
    canonicalize_country_code,
    canonicalize_location_type,
    extract_iata_code,
)


class LocationNormalizationTest(unittest.TestCase):
    def test_extract_iata_code_from_hyphenated_provider_location_id(self) -> None:
        self.assertEqual(
            extract_iata_code({'provider_location_id': 'MA-CAS-CMN'}),
            'CMN',
        )

    def test_extract_iata_code_prefers_actual_three_letter_airport_code(self) -> None:
        self.assertEqual(
            extract_iata_code({'provider_location_id': 'UAE-DXB-TERM1'}),
            'DXB',
        )

    def test_canonicalize_country_code_resolves_country_names(self) -> None:
        self.assertEqual(canonicalize_country_code('', 'Italy'), 'IT')
        self.assertEqual(canonicalize_country_code(None, 'Spain'), 'ES')

    def test_canonicalize_location_type_detects_bus_station_without_false_busan_match(self) -> None:
        self.assertEqual(canonicalize_location_type(None, 'Valencia Bus Station'), 'bus_station')
        self.assertEqual(canonicalize_location_type(None, 'Busan Downtown'), 'downtown')


if __name__ == '__main__':
    unittest.main()
