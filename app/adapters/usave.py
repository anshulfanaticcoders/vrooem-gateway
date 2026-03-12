"""USave adapter — Same API as GreenMotion with different credentials."""

from app.adapters.green_motion import GreenMotionAdapter
from app.adapters.registry import register_adapter
from app.core.config import get_settings


@register_adapter
class USaveAdapter(GreenMotionAdapter):
    supplier_id = "usave"
    supplier_name = "USave"

    def _build_xml(self, request_type: str, body: str) -> str:
        settings = get_settings()
        return f"""<?xml version="1.0" encoding="utf-8"?>
<gm_webservice>
    <header>
        <username>{settings.usave_username}</username>
        <password>{settings.usave_password}</password>
        <version>1.5</version>
    </header>
    <request type="{request_type}">
        {body}
    </request>
</gm_webservice>"""

    def _api_url(self) -> str:
        return get_settings().usave_api_url
