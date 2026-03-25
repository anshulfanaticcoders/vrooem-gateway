import unittest

from app.services.host_bootstrap import extract_public_hosts, parse_doh_ipv4


class HostBootstrapTest(unittest.TestCase):
    def test_extract_public_hosts_ignores_internal_and_blank_urls(self):
        env = {
            'LOCAUTO_API_URL': 'https://nextrent.locautorent.com/webservices/nextRentOTAService.asmx',
            'RECORDGO_AUTH_URL': 'https://auth.recordgo.com/oauth/token',
            'REDIS_URL': 'redis://redis:6379/0',
            'LARAVEL_BASE_URL': 'http://localhost:8000',
            'EMPTY_URL': '',
            'NOT_A_URL': 'value',
        }

        self.assertEqual(
            extract_public_hosts(env),
            [
                'auth.recordgo.com',
                'nextrent.locautorent.com',
            ],
        )

    def test_parse_doh_ipv4_returns_first_a_record(self):
        payload = {
            'Status': 0,
            'Answer': [
                {'name': 'nextrent.locautorent.com.', 'type': 1, 'TTL': 3600, 'data': '72.146.242.185'},
                {'name': 'nextrent.locautorent.com.', 'type': 1, 'TTL': 3600, 'data': '72.146.242.186'},
            ],
        }

        self.assertEqual(parse_doh_ipv4(payload), '72.146.242.185')

    def test_parse_doh_ipv4_returns_none_when_no_a_record_exists(self):
        payload = {
            'Status': 3,
            'Authority': [
                {'name': 'locautorent.com.', 'type': 6, 'TTL': 1800, 'data': 'dns.technorail.com.'},
            ],
        }

        self.assertIsNone(parse_doh_ipv4(payload))


if __name__ == '__main__':
    unittest.main()
