
import os
from decimal import Decimal, InvalidOperation

from flask import (
    Blueprint,
    request,
    render_template,
    flash,
    url_for,
    redirect,
    current_app,
)

from flask_login import login_required

from models.models import Status
from services.domain.inquiry_service import InquiryService


inquiries_bp = Blueprint(
    "inquiries",
    __name__,
    url_prefix="/inquiries",
)


def _attach_image_info(inquiry):
    """
    Attach image_url and image_exists to an inquiry.

    Supports both:
    - Old records containing a full file path.
    - New records containing only the filename.

    Does not modify the database.
    """

    inquiry.image_url = None
    inquiry.image_exists = False

    if not inquiry.prescription_img:
        return inquiry

    filename = os.path.basename(inquiry.prescription_img)

    uploads_dir = os.path.join(
        current_app.root_path,
        "static",
        "uploads",
    )

    full_path = os.path.join(
        uploads_dir,
        filename,
    )

    if (
        os.path.isfile(full_path)
        and os.path.getsize(full_path) > 0
    ):
        inquiry.image_exists = True

        inquiry.image_url = url_for(
            "static",
            filename=f"uploads/{filename}",
        )

    return inquiry


def _parse_discount_percentage(raw_value):
    """
    Parse an optional manually entered discount percentage.

    Empty value -> 0%

    Valid values:
        0 <= percentage <= 100

    Returns:
        Decimal

    Raises:
        ValueError for invalid percentages.
    """

    value = str(raw_value or "").strip()

    # No discount entered means no discount.
    if not value:
        return Decimal("0")

    try:
        percentage = Decimal(value)

    except InvalidOperation:
        raise ValueError(
            "نسبة الخصم غير صحيحة. يرجى إدخال رقم."
        )

    if not percentage.is_finite():
        raise ValueError(
            "نسبة الخصم غير صحيحة."
        )

    if (
        percentage < Decimal("0")
        or percentage > Decimal("100")
    ):
        raise ValueError(
            "نسبة الخصم يجب أن تكون بين 0 و100."
        )

    return percentage


# ============================================================
# LIST INQUIRIES
# ============================================================

@inquiries_bp.route("/")
@login_required
def list_inquiries():

    page = request.args.get(
        "page",
        1,
        type=int,
    )

    search = request.args.get(
        "search",
        "",
    ).strip()

    status = request.args.get(
        "status",
        "",
    )

    inquiry_service = InquiryService()

    pagination, _ = inquiry_service.get_all_inquiries(
        page=page,
        per_page=10,
        search=search or None,
        status=status or None,
    )

    for inquiry in pagination.items:
        _attach_image_info(inquiry)

    stats = inquiry_service.get_stats()

    return render_template(
        "inquiries/list.html",
        inquiries=pagination.items,
        pagination=pagination,
        search=search,
        status=status,
        statuses=Status,
        stats=stats,
    )


# ============================================================
# INQUIRY DETAILS
# ============================================================

@inquiries_bp.route("/<int:inquiry_id>")
@login_required
def inquiry_detail(inquiry_id):

    inquiry_service = InquiryService(
        inquiry_id=inquiry_id
    )

    result = inquiry_service.get_inquiry()

    if not result.success:

        flash(
            result.message,
            "error",
        )

        return redirect(
            url_for("inquiries.list_inquiries")
        )

    _attach_image_info(result.inquiry)

    return render_template(
        "inquiries/detail.html",
        inquiry=result.inquiry,
    )


# ============================================================
# UPDATE INQUIRY STATUS
# ============================================================

@inquiries_bp.route(
    "/<int:inquiry_id>/status",
    methods=["POST"],
)
@login_required
def update_inquiry_status(inquiry_id):

    new_status = request.form.get("status")

    inquiry_service = InquiryService(
        inquiry_id=inquiry_id
    )

    result = inquiry_service.update_status(
        new_status
    )

    flash(
        result.message,
        "success" if result.success else "error",
    )

    return redirect(
        request.referrer
        or url_for("inquiries.list_inquiries")
    )


# ============================================================
# CONFIRM INQUIRY WITH MANUAL DISCOUNT
# ============================================================

@inquiries_bp.route(
    "/<int:inquiry_id>/confirm",
    methods=["POST"],
)
@login_required
def confirm_inquiry(inquiry_id):

    # --------------------------------------------------------
    # 1. Selected medical services
    # --------------------------------------------------------

    selected_service_ids = request.form.getlist(
        "selected_services"
    )

    if not selected_service_ids:

        flash(
            "يرجى تحديد خدمة واحدة على الأقل.",
            "error",
        )

        return redirect(
            url_for(
                "inquiries.inquiry_detail",
                inquiry_id=inquiry_id,
            )
        )

    # --------------------------------------------------------
    # 2. Read manually entered discount percentage
    # --------------------------------------------------------

    raw_discount = request.form.get(
        "discount_percentage",
        "",
    )

    try:

        discount_percentage = _parse_discount_percentage(
            raw_discount
        )

    except ValueError as exc:

        flash(
            str(exc),
            "error",
        )

        return redirect(
            url_for(
                "inquiries.inquiry_detail",
                inquiry_id=inquiry_id,
            )
        )

    # --------------------------------------------------------
    # 3. Confirm inquiry through the service
    # --------------------------------------------------------

    inquiry_service = InquiryService(
        inquiry_id=inquiry_id
    )

    success, message = inquiry_service.confirm_and_reply(
        selected_service_ids,
        discount_percentage=discount_percentage,
    )

    # --------------------------------------------------------
    # 4. Show result
    # --------------------------------------------------------

    flash(
        message,
        "success" if success else "error",
    )

    return redirect(
        url_for(
            "inquiries.inquiry_detail",
            inquiry_id=inquiry_id,
        )
    )
