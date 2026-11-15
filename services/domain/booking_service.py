
import logging
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from io import BytesIO
from typing import Optional
from decimal import Decimal, InvalidOperation
import openpyxl
from sqlalchemy.exc import IntegrityError

from models.models import Booking, Status, db
from services.domain.ticket_service import generate_booking_img
from utils.text_utils import make_reference_id
from utils.excel_utils import format_excel_sheet

logger = logging.getLogger(__name__)


@dataclass
class BookingSaveResult:
    reference_id: str
    image_bytes: bytes
    booking_id: int


class BookingService:
    def __init__(self, booking_id=None, reference_id=None, booking=None):
        """Initialize with a booking ID, reference ID, or loaded booking."""
        self.booking_id = booking_id
        self.reference_id = reference_id
        self._booking = booking

        if booking:
            self.booking_id = booking.id
            self.reference_id = booking.reference_id

    @property
    def booking(self):
        """Lazily load the booking by ID or reference ID."""
        if self._booking is None:
            if self.booking_id is not None:
                self._booking = db.session.get(Booking, self.booking_id)

            elif self.reference_id is not None:
                self._booking = Booking.query.filter_by(
                    reference_id=self.reference_id
                ).first()

        return self._booking

    def display_bookings(
        self,
        page=1,
        per_page=10,
        search=None,
        status=None,
        date_from=None,
        date_to=None,
    ):
        """
        Fetch paginated bookings using the provided filters.

        Search supports both primary and secondary phone numbers.
        Without search or date filters, only the last 30 days are returned.
        """

        query = Booking.query

        if not date_from and not date_to and not search:
            before_30_days = datetime.now(timezone.utc) - timedelta(days=30)
            query = query.filter(
                Booking.booking_time >= before_30_days
            )

        if search:
            query = query.filter(
                db.or_(
                    Booking.name.ilike(f"%{search}%"),
                    Booking.phone_number.ilike(f"%{search}%"),
                    Booking.secondary_phone_number.ilike(f"%{search}%"),
                )
            )

        if status:
            try:
                query = query.filter(Booking.status == Status(status))
            except ValueError:
                pass

        if date_from:
            query = query.filter(Booking.date >= date_from)

        if date_to:
            query = query.filter(Booking.date <= date_to)

        pagination = query.order_by(
            Booking.booking_time.desc()
        ).paginate(
            page=page,
            per_page=per_page,
            error_out=False,
        )

        return pagination, "تم العثور على الحجوزات"

    def update_booking_status(self, new_status: Status, booking_id=None):
        """Update a booking's status and return the updated booking."""

        if isinstance(new_status, int) or isinstance(booking_id, Status):
            actual_booking_id, actual_status = new_status, booking_id
        else:
            actual_booking_id = booking_id or self.booking_id
            actual_status = new_status

        booking = None

        if self._booking and (
            not actual_booking_id or self._booking.id == actual_booking_id
        ):
            booking = self._booking

        elif actual_booking_id:
            booking = db.session.get(Booking, actual_booking_id)

        if not booking:
            return None, "الحجز غير موجود"

        try:
            booking.status = actual_status
            db.session.commit()

            self._booking = booking
            self.booking_id = booking.id

            return booking, "تم تحديث الحالة"

        except Exception:
            db.session.rollback()
            logger.exception("[BookingService.update_booking_status] Error")
            return None, "حدث خطأ أثناء تحديث الحالة"

    def save_booking(
        self,
        name: str,
        phone: str,
        date: str,
        details: str,
        comes_from: str,
        time: str,
        address: str,
        secondary_phone_number: Optional[str] = None,
        total: Optional[Decimal] = None,
    ) -> BookingSaveResult:
        """
        Save booking with optional total price
        and generate confirmation image.
        """

        secondary_phone_number = (
            secondary_phone_number.strip()
            if secondary_phone_number
            and secondary_phone_number.strip()
            else None
        )

        # Normalize total price
        if total is not None and str(total).strip() != "":
            try:
                total = Decimal(str(total)).quantize(Decimal("0.01"))

                if not total.is_finite() or total < 0:
                    raise ValueError("Invalid booking total")

                if total > Decimal("99999999.99"):
                    raise ValueError("Booking total exceeds allowed limit")

            except (InvalidOperation, ValueError):
                logger.exception(
                    "[BookingService.save_booking] Invalid total: %s",
                    total,
                )
                raise ValueError("Invalid booking total")
        else:
            total = None

        reference_id = make_reference_id()

        while Booking.query.filter_by(
            reference_id=reference_id
        ).first() is not None:
            reference_id = make_reference_id()

        booking = Booking(
            name=name,
            phone_number=phone,
            secondary_phone_number=secondary_phone_number,
            date=date,
            details=details,
            comes_from=comes_from,
            reference_id=reference_id,
            booking_time=datetime.now(timezone.utc),
            time=time,
            address=address,
            total=total,
        )

        db.session.add(booking)

        try:
            db.session.commit()

        except IntegrityError:
            db.session.rollback()
            logger.exception(
                "[BookingService.save_booking] IntegrityError "
                "(reference_id=%s)",
                reference_id,
            )
            raise

        except Exception:
            db.session.rollback()
            logger.exception(
                "[BookingService.save_booking] Error committing booking"
            )
            raise

        self._booking = booking
        self.booking_id = booking.id
        self.reference_id = reference_id

        image_bytes = generate_booking_img(
            name=booking.name,
            phone=booking.phone_number,
            secondary_phone_number=booking.secondary_phone_number,
            date=booking.date,
            details=booking.details,
            reference_id=booking.reference_id,
            time=booking.time,
            address=booking.address,
        )

        return BookingSaveResult(
            reference_id=reference_id,
            image_bytes=image_bytes,
            booking_id=booking.id,
        )

    def export_to_excel(self, search, status, date_from, date_to):
        """Generate an in-memory Excel workbook with both phone numbers."""

        pagination, _ = self.display_bookings(
            page=1,
            per_page=99999,
            search=search,
            status=status,
            date_from=date_from,
            date_to=date_to,
        )

        wb = openpyxl.Workbook()
        ws = wb.active

        headers = [
            "الرقم المرجعي",
            "الاسم",
            "رقم الهاتف الأساسي",
            "رقم الهاتف البديل",
            "العنوان",
            "التفاصيل",
            "تاريخ الزيارة",
            "الوقت",
            "المنصة",
            "وقت الإنشاء",
            "إجمالي الحجز",
            "الحالة",
        ]

        rows = []

        for booking in pagination.items:
            rows.append([
                booking.reference_id or f"#{booking.id}",
                booking.name,
                booking.phone_number,
                booking.secondary_phone_number or "",
                booking.address,
                booking.details,
                booking.date,
                booking.time,
                booking.comes_from or "غير محدد",
                (
                    booking.booking_time.strftime("%Y-%m-%d %H:%M")
                    if booking.booking_time
                    else ""
                ),
                float(booking.total) if booking.total is not None else "",
                booking.status.value if booking.status else "",
            ])

        format_excel_sheet(
            ws,
            title="الحجوزات المنزلية",
            headers=headers,
            rows=rows,
            status_col_idx=12,
        )

        output = BytesIO()
        wb.save(output)
        output.seek(0)

        return output

    @staticmethod
    def get_total_bookings_count() -> int:
        """Return the total number of bookings."""
        try:
            return Booking.query.count()
        except Exception:
            logger.exception(
                "[BookingService.get_total_bookings_count] Error"
            )
            return 0

    @staticmethod
    def get_booking_by_ref(reference_id: str):
        """Get a booking by its reference ID."""
        if not reference_id:
            return None

        try:
            return Booking.query.filter_by(
                reference_id=reference_id
            ).first()

        except Exception:
            logger.exception(
                "[BookingService.get_booking_by_ref] Error"
            )
            return None
