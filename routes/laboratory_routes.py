
from flask import (
    Blueprint,
    render_template,
    request,
    flash,
    redirect,
    url_for,
    abort,
    current_app
)

from flask_login import login_required

from models.models import db, Branch
from services.domain.laboratory_service import LaboratoryService


laboratory_bp = Blueprint(
    "laboratory",
    __name__,
    url_prefix="/laboratories"
)


# ==============================================
# HELPERS
# ==============================================

def get_available_laboratories():
    """
    Use the original LaboratoryService.
    """
    service = LaboratoryService()
    laboratories, _ = service.get_all_laboratories()
    return laboratories or []


def get_accessible_laboratory(lab_id):
    """
    Check that the laboratory is returned by
    the existing LaboratoryService.

    This preserves the current service visibility.
    """
    for lab in get_available_laboratories():
        if lab.id == lab_id:
            return lab

    abort(404)


def branch_redirect(lab_id):
    return redirect(
        url_for(
            "laboratory.list_laboratories",
            laboratory_id=lab_id
        )
    )


def read_branch_form():
    name = request.form.get("name", "").strip()
    address = request.form.get("address", "").strip()
    phone = request.form.get("phone_number", "").strip()
    hours = request.form.get("working_hours", "").strip()

    if not name:
        return None, "اسم الفرع مطلوب"

    if not address:
        return None, "عنوان الفرع مطلوب"

    if len(name) > 100:
        return None, "اسم الفرع طويل جدًا"

    if len(address) > 255:
        return None, "عنوان الفرع طويل جدًا"

    if len(phone) > 50:
        return None, "رقم الهاتف طويل جدًا"

    if len(hours) > 200:
        return None, "مواعيد العمل طويلة جدًا"

    return {
        "name": name,
        "address": address,
        "phone_number": phone or None,
        "working_hours": hours or None
    }, None


# ==============================================
# LABORATORY LIST
# ==============================================

@laboratory_bp.route("/")
@login_required
def list_laboratories():

    laboratories = get_available_laboratories()

    selected_id = request.args.get(
        "laboratory_id",
        type=int
    )

    current_lab = None

    if selected_id is not None:
        current_lab = next(
            (
                lab for lab in laboratories
                if lab.id == selected_id
            ),
            None
        )

    if current_lab is None and laboratories:
        current_lab = laboratories[0]

    branches = []

    if current_lab:
        branches = (
            Branch.query
            .filter_by(laboratory_id=current_lab.id)
            .order_by(Branch.id)
            .all()
        )

    return render_template(
        "laboratory/list.html",
        laboratories=laboratories,
        current_lab=current_lab,
        branches=branches
    )


# ==============================================
# CREATE LABORATORY - ORIGINAL BEHAVIOR
# ==============================================

@laboratory_bp.route(
    "/create",
    methods=["GET", "POST"]
)
@login_required
def create_laboratory():

    if request.method == "POST":

        name = request.form.get("name", "").strip()
        location = request.form.get("location", "").strip()
        description = request.form.get(
            "description", ""
        ).strip()

        lab_service = LaboratoryService()

        lab, msg = lab_service.create_laboratory(
            name,
            location,
            description
        )

        if lab:
            flash(msg, "success")
            return redirect(
                url_for("laboratory.list_laboratories")
            )

        flash(msg, "error")

    return render_template("laboratory/create.html")


# ==============================================
# EDIT LABORATORY - ORIGINAL BEHAVIOR
# ==============================================

@laboratory_bp.route(
    "/<int:lab_id>/edit",
    methods=["GET", "POST"]
)
@login_required
def edit_laboratory(lab_id):

    lab_service = LaboratoryService(lab_id=lab_id)

    lab, msg = lab_service.get_laboratory()

    if not lab:
        flash(msg, "error")
        return redirect(
            url_for("laboratory.list_laboratories")
        )

    if request.method == "POST":

        name = request.form.get("name", "").strip()
        location = request.form.get("location", "").strip()
        description = request.form.get(
            "description", ""
        ).strip()

        updated_lab, update_msg = (
            lab_service.update_laboratory(
                name=name,
                location=location,
                description=description
            )
        )

        if updated_lab:
            flash(update_msg, "success")
            return redirect(
                url_for("laboratory.list_laboratories")
            )

        flash(update_msg, "error")

    return render_template(
        "laboratory/edit.html",
        laboratory=lab
    )


# ==============================================
# CREATE BRANCH
# ==============================================

@laboratory_bp.route(
    "/branches/create",
    methods=["POST"]
)
@login_required
def create_branch():

    lab_id = request.form.get(
        "laboratory_id",
        type=int
    )

    if lab_id is None:
        abort(400)

    get_accessible_laboratory(lab_id)

    data, error = read_branch_form()

    if error:
        flash(error, "error")
        return branch_redirect(lab_id)

    branch = Branch(
        laboratory_id=lab_id,
        **data
    )

    try:
        db.session.add(branch)
        db.session.commit()

        flash(
            "تمت إضافة الفرع بنجاح",
            "success"
        )

    except Exception:
        db.session.rollback()

        current_app.logger.exception(
            "Failed to create branch"
        )

        flash(
            "حدث خطأ أثناء إضافة الفرع",
            "error"
        )

    return branch_redirect(lab_id)


# ==============================================
# EDIT BRANCH
# ==============================================

@laboratory_bp.route(
    "/branches/<int:branch_id>/edit",
    methods=["POST"]
)
@login_required
def edit_branch(branch_id):

    branch = db.session.get(Branch, branch_id)

    if not branch:
        abort(404)

    lab_id = branch.laboratory_id

    get_accessible_laboratory(lab_id)

    data, error = read_branch_form()

    if error:
        flash(error, "error")
        return branch_redirect(lab_id)

    branch.name = data["name"]
    branch.address = data["address"]
    branch.phone_number = data["phone_number"]
    branch.working_hours = data["working_hours"]

    try:
        db.session.commit()

        flash(
            "تم تحديث بيانات الفرع بنجاح",
            "success"
        )

    except Exception:
        db.session.rollback()

        current_app.logger.exception(
            "Failed to update branch"
        )

        flash(
            "حدث خطأ أثناء تعديل الفرع",
            "error"
        )

    return branch_redirect(lab_id)


# ==============================================
# DELETE BRANCH
# ==============================================

@laboratory_bp.route(
    "/branches/<int:branch_id>/delete",
    methods=["POST"]
)
@login_required
def delete_branch(branch_id):

    branch = db.session.get(Branch, branch_id)

    if not branch:
        abort(404)

    get_accessible_laboratory(branch.laboratory_id)

    try:
        db.session.delete(branch)
        db.session.commit()

    except Exception:
        db.session.rollback()

        current_app.logger.exception(
            "Failed to delete branch"
        )

        return "Failed to delete branch", 500

    return "", 204
