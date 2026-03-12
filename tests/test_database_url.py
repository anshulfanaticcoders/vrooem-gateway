import unittest

from app.db.database_url import build_connect_args, clean_database_url


class DatabaseUrlTest(unittest.TestCase):
    def test_remote_database_without_ssl_flag_does_not_force_ssl(self) -> None:
        url = "postgresql+asyncpg://postgres:secret@ic08ggkksokgss0o0osc84g8:5432/postgres"

        self.assertEqual(build_connect_args(url), {})

    def test_remote_database_with_ssl_require_enables_ssl(self) -> None:
        url = "postgresql+asyncpg://postgres:secret@aws-1-ap-northeast-1.pooler.supabase.com:5432/postgres?ssl=require"

        connect_args = build_connect_args(url)

        self.assertIn("ssl", connect_args)

    def test_clean_database_url_strips_ssl_require_query(self) -> None:
        url = "postgresql+asyncpg://postgres:secret@aws-1-ap-northeast-1.pooler.supabase.com:5432/postgres?ssl=require"

        self.assertEqual(
            clean_database_url(url),
            "postgresql+asyncpg://postgres:secret@aws-1-ap-northeast-1.pooler.supabase.com:5432/postgres",
        )

