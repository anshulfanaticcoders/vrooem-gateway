"""Application configuration loaded from environment variables."""

from functools import lru_cache

from pydantic_settings import BaseSettings

LOCAL_ENVS = {"local", "dev", "development", "test", "testing"}


class Settings(BaseSettings):
    # ─── Gateway ───
    gateway_env: str = "local"
    gateway_debug: bool = False
    gateway_api_keys: str = "dev_key_change_me"
    gateway_secret: str = "hmac_secret_change_me"
    cors_allowed_origins: str = ""
    provider_cors_allowed_origins: str = ""
    allow_insecure_supplier_tls: bool = False
    allow_insecure_redis_tls: bool = False

    # ─── MySQL (Laravel — provider API tables) ───
    mysql_url: str = "mysql+aiomysql://root@localhost:3306/carrental"

    # ─── Redis ───
    redis_url: str = "redis://localhost:6379/0"
    search_cache_ttl: int = 60
    location_refresh_provider_timeout_seconds: float = 180.0

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

    # ─── EMR Car Rental ───
    emr_api_url: str = ""
    emr_username: str = ""
    emr_password: str = ""
    emr_token: str = ""

    # ─── Click2Rent ───
    click2rent_email: str = ""
    click2rent_password: str = ""

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
    surprice_fdw_rate_code: str = ""

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

    # ─── Easirent ───
    easirent_api_url: str = "https://easirent.com/broker/Vrooem/Livefeed.asp"
    easirent_account_us_domestic: str = "$USA202"
    easirent_account_us_inbound: str = "$USA202A"
    easirent_account_roi: str = "$ROI202"

    @property
    def api_keys_list(self) -> list[str]:
        """Parse comma-separated API keys."""
        return [k.strip() for k in self.gateway_api_keys.split(",") if k.strip()]

    @property
    def is_local_env(self) -> bool:
        return self.gateway_env.lower() in LOCAL_ENVS

    @property
    def internal_cors_origins(self) -> list[str]:
        if self.cors_allowed_origins.strip():
            return [origin.strip() for origin in self.cors_allowed_origins.split(",") if origin.strip()]
        if self.gateway_debug or self.is_local_env:
            return ["*"]
        return [self.laravel_base_url]

    @property
    def provider_cors_origins(self) -> list[str]:
        if self.provider_cors_allowed_origins.strip():
            return [origin.strip() for origin in self.provider_cors_allowed_origins.split(",") if origin.strip()]
        if self.gateway_debug or self.is_local_env:
            return ["*"]
        return [self.laravel_base_url]

    @property
    def supplier_tls_verify(self) -> bool:
        return not (self.is_local_env or self.allow_insecure_supplier_tls)

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


@lru_cache
def get_settings() -> Settings:
    return Settings()


def validate_runtime_settings(settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    if settings.is_local_env:
        return

    if not settings.api_keys_list or "dev_key_change_me" in settings.api_keys_list:
        raise RuntimeError("GATEWAY_API_KEYS must be configured with non-default values outside local/dev.")
    if settings.gateway_secret == "hmac_secret_change_me":
        raise RuntimeError("GATEWAY_SECRET must be configured with a non-default value outside local/dev.")
    if not settings.laravel_api_token:
        raise RuntimeError("LARAVEL_API_TOKEN must be configured outside local/dev.")
