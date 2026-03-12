"""Pricing schemas for vehicle rental pricing."""

from pydantic import BaseModel, Field

from app.schemas.common import PaymentOption


class Fee(BaseModel):
    """An individual fee or charge."""

    name: str
    amount: float
    currency: str = "EUR"
    included_in_total: bool = True
    description: str = ""


class Pricing(BaseModel):
    """Canonical pricing information for a vehicle."""

    currency: str = "EUR"
    total_price: float = Field(ge=0, description="Total rental price for the full period")
    daily_rate: float = Field(ge=0, description="Price per day")
    price_includes_tax: bool = True
    fees: list[Fee] = Field(default_factory=list)
    payment_options: list[PaymentOption] = Field(
        default_factory=lambda: [PaymentOption.PAY_AT_PICKUP]
    )
    deposit_amount: float | None = Field(
        default=None, description="Required deposit/excess amount"
    )
    deposit_currency: str | None = None
