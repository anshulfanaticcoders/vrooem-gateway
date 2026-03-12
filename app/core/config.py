"""Application configuration loaded from environment variables."""

from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # ─── Gateway ───
    gateway_env: str = "local"
    gateway_debug: bool = False
    gateway_api_keys: str = "dev_key_change_me"
    gateway_secret: str = "hmac_secret_change_me"

    # ─── Database ───
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/vrooem_gateway"

    # ─── Redis ───
    redis_url: str = "redis://localhost:6379/0"
    search_cache_ttl: int = 60

    # ─── Laravel ───
    laravel_base_url: str = "http://localhost:8000"
    laravel_api_token: str = ""

    # ─── GreenMotion ───
    greenmotion_api_url: str = ""
    greenmotion_username: str = ""
    greenmotion_password: str = ""

    # ─── USave (same API as GreenMotion, different credentials) ───
    usave_api_url: str = ""
    usave_username: str = ""
    usave_password: str = ""

    # ─── Renteon ───
    renteon_api_url: str = ""
    renteon_username: str = ""
    renteon_password: str = ""
    renteon_allowed_providers: str = "LetsDrive,CapitalCarRental,LuxGoo,Alquicoche"
    renteon_pricelist_codes: str = ""  # Comma-separated pricelist codes

    # ─── Favrica ───
    favrica_api_url: str = ""
    favrica_token: str = ""
    favrica_username: str = ""
    favrica_password: str = ""

    # ─── XDrive ───
    xdrive_api_url: str = ""
    xdrive_token: str = ""
    xdrive_username: str = ""
    xdrive_password: str = ""

    # ─── Adobe Car ───
    adobe_api_url: str = ""
    adobe_username: str = ""
    adobe_password: str = ""

    # ─── OK Mobility ───
    okmobility_api_url: str = ""
    okmobility_customer_code: str = ""
    okmobility_company_code: str = ""

    # ─── Locauto Rent ───
    locauto_api_url: str = ""
    locauto_username: str = ""
    locauto_password: str = ""

    # ─── Wheelsys ───
    wheelsys_api_url: str = ""
    wheelsys_account_no: str = ""
    wheelsys_link_code: str = ""
    wheelsys_agent_code: str = ""

    # ─── Surprice ───
    surprice_api_url: str = ""
    surprice_api_key: str = ""
    surprice_rate_code: str = ""

    # ─── Sicily By Car ───
    sicilybycar_api_url: str = ""
    sicilybycar_account_code: str = ""
    sicilybycar_api_key: str = ""

    # ─── Record Go ───
    recordgo_api_url: str = ""
    recordgo_auth_url: str = ""
    recordgo_client_id: str = ""
    recordgo_client_secret: str = ""
    recordgo_subscription_key: str = ""
    recordgo_partner_user: str = ""
    recordgo_sell_codes: str = ""  # JSON override for sell codes, e.g. {"ES":95,"IC":96}

    @property
    def api_keys_list(self) -> list[str]:
        """Parse comma-separated API keys."""
        return [k.strip() for k in self.gateway_api_keys.split(",") if k.strip()]

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
