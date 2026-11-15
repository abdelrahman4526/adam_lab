
import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional, Any

from langchain_core.tools import tool

from services.domain.booking_service import BookingService


logger = logging.getLogger(__name__)


@dataclass
class SaveVisitResult:
    success: bool
    visit: Any = None
    message: Optional[str] = None


@tool
def save_visit_tool(
    name: str,
    phone_number: str,
    address: str,
    details: str,
    date: str,
    time: str,
    total: float,
    comes_from: str = "WhatsApp",
    secondary_phone_number: Optional[str] = None,
) -> SaveVisitResult:
    """
    Saves a confirmed home visit medical laboratory booking.

    Required:
        name
        phone_number
        address
        details
        date
        time

    Optional:
        comes_from
        secondary_phone_number
        total: Confirmed booking total in EGP.
               None if the total has not been calculated.

    IMPORTANT:
        total must come from an actual price calculation.
        Never invent or estimate the booking total.
    """

    try:
        # 1. Normalize optional secondary phone
        secondary_phone_number = (
            secondary_phone_number.strip()
            if secondary_phone_number and secondary_phone_number.strip()
            else None
        )

        # 2. Normalize booking total
        normalized_total = (
            Decimal(str(total))
            if total is not None
            else None
        )

        # 3. Save booking
        booking_service = BookingService()

        result = booking_service.save_booking(
            name=name,
            phone=phone_number,
            secondary_phone_number=secondary_phone_number,
            date=date,
            details=details,
            comes_from=comes_from,
            time=time,
            address=address,
            total=normalized_total,
        )

        # 4. Make sure booking was saved successfully
        if not result or not result.reference_id:
            return SaveVisitResult(
                success=False,
                message="Failed to generate booking reference.",
            )

        # 5. Return booking result
        return SaveVisitResult(
            success=True,
            visit=result,
            message="Booking saved successfully.",
        )

    except Exception as e:
        logger.exception("Error inside save_visit_tool")

        return SaveVisitResult(
            success=False,
            message=str(e),
        )
