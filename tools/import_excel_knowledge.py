"""
bulk_import_tests.py

Script لاستيراد التحاليل من Excel (name / general_price_max / el_adel_price)
باستخدام نفس الـ pipeline الموجود عندك بالظبط:
    generate_test()  ->  TestsService.create_lab_service()

يعني كل تحليل هياخد وصف + تعليمات + keywords + aliases + sample_type + duration
من الـ AI بالظبط زي لما تعمله يدوي من الفورم، وبعدين يتعمله insert في الداتابيز
وupsert في Qdrant تلقائي (لأن create_lab_service بيعمل ده جوه).

--------------------------------------------------------------------------
قبل ما تشغله:
1) عدّل الـ import بتاع الـ Flask app تحت (IMPORT الخاص بالـ app/db) على حسب
   شكل app.py عندك بالظبط (create_app() ولا app جاهز).
2) الاسكريبت بياخد أول Laboratory موجود في الداتابيز تلقائي (زي ما قولت
   "أول واحد في الـ db مفيش غيره"). لو حبيت تحدد لاب معين غيّر GET_LAB_STRATEGY.
3) الملف لازم يحتوي على أعمدة: name, general_price_max, el_adel_price.
   general_price_min بيتحسب بخصم 20% في TestsService.
4) جرب الأول بعدد صغير: 
       python bulk_import_tests.py --limit 5
   وبعدين لما تتأكد إنه شغال صح شغله كامل:
       python bulk_import_tests.py

المزايا:
- Resume تلقائي: لو الاسكريبت اتقفل في النص، شغله تاني وهيكمل من غير ما يعيد
  التحاليل اللي خلصت (بيتأكد من الاسم في الداتابيز قبل لما يستهلك AI call).
- بيسجل كل تحليل فشل في ملف failed_tests.csv عشان تراجعه/تعيد تشغيله لوحده.
- بينام delay بسيط بين كل استدعاء AI عشان ميضربش rate limit.
- بيكتب log تفصيلي (import_log.txt افتراضيًا، أو حدده بـ --log-file) فيه لكل
  تحليل: اسمه، نتيجة التوليد من Gemini كاملة، النص اللي اتحول embedding،
  اللي اتحفظ في الداتابيز، والـ metadata اللي راحت (أو حاولت تروح) لـ Qdrant.
"""

import argparse
import csv
import inspect
import sys
import time
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import openpyxl

# --------------------------------------------------------------------------
# 1) IMPORT بتاع الـ Flask app والـ db - عدّل السطرين دول على حسب app.py عندك
# --------------------------------------------------------------------------
try:
    # لو عندك app.py فيه app جاهز (Flask(__name__) على مستوى الملف)
    from app import app  # noqa: E402
except ImportError:
    # لو عندك factory pattern: def create_app(): ...
    from app import create_app  # noqa: E402
    app = create_app()

from models.models import db, Laboratory, LabService  # noqa: E402
import services.domain.tests_service as tests_service_module  # noqa: E402
from services.domain.tests_service import TestsService  # noqa: E402
from services.shared.generation_service import generate_test  # noqa: E402
from services.shared.vector_service import upsert_test_vector as _real_upsert_test_vector  # noqa: E402

try:
    from utils.text_utils import build_test_text
except ImportError:
    # fallback بسيط لو اتغير اسم/مكان الدالة - نفس منطق التركيب المتوقع
    def build_test_text(name, description, keywords):
        return f"{name}. {description or ''} {' '.join(keywords or [])}".strip()


# --------------------------------------------------------------------------
# إعدادات
# --------------------------------------------------------------------------
DEFAULT_EXCEL_PATH = "Lab_Prices_2026_No_Zero_Prices.xlsx"
FAILED_LOG_PATH = "failed_tests.csv"
DEFAULT_DETAIL_LOG_PATH = "import_log.txt"
SLEEP_BETWEEN_CALLS = 1.0  # ثانية بين كل نداء AI - زوّدها لو حصل rate limit


def get_target_laboratory():
    """
    بيرجع أول Laboratory موجود في الداتابيز.
    (حسب كلامك: مفيش غيره دلوقتي)
    """
    lab = Laboratory.query.order_by(Laboratory.id.asc()).first()
    if not lab:
        raise RuntimeError(
            "مفيش أي Laboratory في الداتابيز خالص. "
            "ضيف معمل واحد على الأقل الأول قبل ما تشغل الاسكريبت."
        )
    return lab


MONEY_SCALE = Decimal("0.01")
MAX_PRICE = Decimal("99999999.99")


def parse_money(value, *, optional=False):
    """Read monetary values safely from Excel; blank El Adel prices become NULL."""
    if value is None or (isinstance(value, str) and not value.strip()):
        if optional:
            return None
        raise ValueError("السعر العام مطلوب")
    if isinstance(value, bool):
        raise ValueError("قيمة السعر غير صحيحة")
    try:
        text = str(value).strip().replace(",", "")
        amount = Decimal(text)
        if not amount.is_finite() or amount < 0 or amount > MAX_PRICE:
            raise ValueError("السعر خارج النطاق المسموح")
        return amount.quantize(MONEY_SCALE, rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError) as exc:
        raise ValueError(f"قيمة السعر غير صحيحة: {value!r}") from exc


def normalize_header(header):
    return str(header or "").strip().lower().replace("-", "_").replace(" ", "_")


def read_excel_rows(excel_path: str):
    """Return validated rows from a workbook with both General and El Adel prices.

    Required columns (case insensitive): name, general_price_max, el_adel_price.
    Supported aliases: test_name, general_price, adel_price.
    Empty El Adel -> None. Missing/zero max price or explicit zero Adel -> skip.
    """
    wb = openpyxl.load_workbook(excel_path, data_only=True, read_only=True)
    try:
        ws = wb.active
        iterator = ws.iter_rows(values_only=True)
        try:
            headers = next(iterator)
        except StopIteration:
            raise ValueError("ملف Excel فاضي")
        col_names = {normalize_header(value): index for index, value in enumerate(headers)}

        def get_column(*names):
            for alias in names:
                if alias in col_names:
                    return col_names[alias]
            return None

        name_col = get_column("name", "test_name", "test")
        max_col = get_column("general_price_max", "general_price", "price_max")
        adel_col = get_column("el_adel_price", "adel_price", "eladelprice", "el_adel")
        if name_col is None or max_col is None or adel_col is None:
            raise ValueError(
                "أعمدة Excel المطلوبة: name, general_price_max, el_adel_price. "
                f"الأعمدة الموجودة: {list(col_names)}. "
                "لازم تستخدم الشيت المدمج الجديد مش الشيت القديم اللي فيه price فقط."
            )

        rows = []
        seen = set()
        for row_num, row in enumerate(iterator, start=2):
            def cell(col):
                return row[col] if col < len(row) else None

            name_value = cell(name_col)
            if name_value is None or not str(name_value).strip():
                continue
            name = str(name_value).strip()
            # Skip repeated headers inside the sheet.
            if normalize_header(name) in ("name", "test_name"):
                continue
            try:
                max_price = parse_money(cell(max_col))
                adel_price = parse_money(cell(adel_col), optional=True)
            except ValueError as exc:
                print(f"⏭️ Excel row {row_num}: {name!r} — {exc}; skipping")
                continue
            if max_price == 0 or adel_price == 0:
                print(f"⏭️ Excel row {row_num}: {name!r} — zero price; skipping")
                continue
            if name.casefold() in seen:
                print(f"⏭️ Excel row {row_num}: {name!r} — duplicate name in Excel; skipping")
                continue
            seen.add(name.casefold())
            rows.append({
                "name": name,
                "general_price_max": max_price,
                "general_price_min": (max_price * Decimal("0.80")).quantize(
                    MONEY_SCALE, rounding=ROUND_HALF_UP
                ),
                "el_adel_price": adel_price,
                "excel_row": row_num,
            })
        return rows
    finally:
        wb.close()


def already_exists(name: str) -> bool:
    # Only query the ID, avoiding a full model fetch when checking duplicates.
    return db.session.query(LabService.id).filter(LabService.name == name).first() is not None


def log_failure(name: str, max_price, adel_price, error: str):
    file_exists = Path(FAILED_LOG_PATH).exists()
    with open(FAILED_LOG_PATH, "a", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["name", "general_price_max", "el_adel_price", "error"])
        writer.writerow([name, max_price, adel_price if adel_price is not None else "", error])


def generation_result_to_dict(generated) -> dict:
    """بيحوّل TestGenerationResult (pydantic) لـ dict عادي، متوافق مع pydantic v1 و v2."""
    if hasattr(generated, "model_dump"):
        return generated.model_dump()
    return generated.dict()


def format_list(value) -> str:
    if not value:
        return "[]"
    return "[" + ", ".join(str(v) for v in value) + "]"


def write_detail_log(log_path: str, index: int, total: int, name: str,
                      generated_dict: dict | None,
                      embed_text: str | None,
                      db_fields: dict | None,
                      db_status: str,
                      qdrant_payload: dict | None,
                      qdrant_status: str):
    """
    بيكتب في ملف الـ log بلوك واحد واضح لكل تحليل فيه بالظبط:
    1) اسم التحليل
    2) نتيجة التوليد من Gemini
    3) النص اللي هيتحول embedding
    4) اللي هيتحفظ في الداتابيز
    5) الـ metadata اللي راحت (أو مراحتش) لـ Qdrant
    """
    lines = []
    lines.append("=" * 70)
    lines.append(f"[{index}/{total}] {name}")
    lines.append("=" * 70)

    lines.append("🧬 نتيجة التوليد من Gemini:")
    if generated_dict is None:
        lines.append("  (لم يتم التوليد - حصل خطأ قبل الوصول للخطوة دي)")
    else:
        lines.append(f"  description          : {generated_dict.get('description', '')}")
        lines.append(f"  patient_instructions  : {generated_dict.get('patient_instructions', '')}")
        lines.append(f"  keywords              : {format_list(generated_dict.get('keywords'))}")
        lines.append(f"  alias_name            : {format_list(generated_dict.get('alias_name'))}")
        lines.append(f"  duration              : {generated_dict.get('duration')}")
        lines.append(f"  sample_type           : {generated_dict.get('sample_type')}")
    lines.append("")

    lines.append("🧠 النص اللي هيتحول لـ embedding:")
    lines.append(f'  "{embed_text}"' if embed_text else "  (لسه ماتحسبش)")
    lines.append("")

    lines.append("💾 اللي هيتحفظ في الداتابيز (LabService):")
    if db_fields is None:
        lines.append("  (لسه ماتحفظش)")
    else:
        for key, val in db_fields.items():
            if isinstance(val, list):
                val = format_list(val)
            lines.append(f"  {key:<20}: {val}")
    lines.append(f"  الحالة: {db_status}")
    lines.append("")

    lines.append("🔎 الـ metadata اللي بعتت (أو محاولة إرسالها) لـ Qdrant:")
    if qdrant_payload is None:
        lines.append("  (لسه محصلش)")
    else:
        for key, val in qdrant_payload.items():
            if isinstance(val, list):
                val = format_list(val)
            lines.append(f"  {key:<12}: {val}")
    lines.append(f"  الحالة: {qdrant_status}")
    lines.append("")
    lines.append("")

    with open(log_path, "a", encoding="utf-8-sig") as f:
        f.write("\n".join(lines))


def main():
    parser = argparse.ArgumentParser(description="Bulk import lab tests from Excel using the AI generation pipeline")
    parser.add_argument("--excel", default=DEFAULT_EXCEL_PATH, help="Path to Excel: name, general_price_max, el_adel_price")
    parser.add_argument("--validate-only", action="store_true", help="Validate Excel rows without AI, DB writes or Qdrant")
    parser.add_argument("--laboratory-id", type=int, default=None, help="Explicit laboratory ID (default: first laboratory)")
    parser.add_argument("--update-existing-prices", action="store_true", help="Update prices for matching existing tests without running AI")
    parser.add_argument("--limit", type=int, default=None, help="Only process the first N rows (for testing)")
    parser.add_argument("--sleep", type=float, default=SLEEP_BETWEEN_CALLS, help="Seconds to sleep between AI calls")
    parser.add_argument("--dry-run", action="store_true", help="Generate with AI but don't insert into DB/Qdrant")
    parser.add_argument(
        "--skip-vectors",
        action="store_true",
        help="Don't attempt to upsert into Qdrant at all (use this while Qdrant is down/unreachable). "
             "Insert into the SQL DB only, then re-index into Qdrant later once it's back up.",
    )
    parser.add_argument(
        "--log-file",
        default=DEFAULT_DETAIL_LOG_PATH,
        help="Path to the detailed per-test log file (name, Gemini result, embedding text, DB fields, Qdrant metadata)",
    )
    args = parser.parse_args()

    if not Path(args.excel).exists():
        print(f"❌ الملف مش موجود: {args.excel}")
        sys.exit(1)

    # Validate workbook before making any AI call or DB mutation.
    try:
        rows = read_excel_rows(args.excel)
    except (ValueError, OSError, KeyError) as exc:
        parser.error(f"تعذر قراءة Excel: {exc}")
    if args.limit is not None:
        if args.limit < 1:
            parser.error("--limit لازم يكون 1 أو أكبر")
        rows = rows[:args.limit]
    print(f"📄 عدد التحاليل الصالحة: {len(rows)}")
    if args.validate_only:
        for row in rows[:5]:
            print(f"  {row['name']}: max={row['general_price_max']}, min={row['general_price_min']}, adel={row['el_adel_price']}")
        print("✅ Excel صالح. لم يتم استدعاء AI أو تعديل MySQL أو Qdrant.")
        return
    if not rows:
        print("❌ مفيش أي تحاليل صالحة للاستيراد")
        return
    params = inspect.signature(TestsService.create_lab_service).parameters
    if not {"general_price_max", "el_adel_price"}.issubset(params):
        parser.error("نسخة TestsService القديمة لا تدعم السعرين الجديدين؛ حدّث tests_service.py أولاً")

    # last_qdrant_call بتتحدث في كل مرة الـ wrapper بيتنادي، عشان نقدر نطبعها في الـ log
    last_qdrant_call = {}

    def logged_upsert_test_vector(test_id, test_name, description, keywords):
        try:
            embed_text = build_test_text(test_name, description, keywords)
        except Exception as e:
            embed_text = f"<تعذر بناء نص الـ embedding: {e}>"

        payload = {
            "test_id": test_id,
            "name": test_name,
            "description": description,
            "keywords": keywords or [],
        }

        if args.skip_vectors:
            last_qdrant_call.update(
                {"embed_text": embed_text, "payload": payload, "status": "اتخطي (--skip-vectors)"}
            )
            return

        try:
            _real_upsert_test_vector(test_id, test_name, description, keywords)
            last_qdrant_call.update(
                {"embed_text": embed_text, "payload": payload, "status": "✅ تم الـ upsert بنجاح"}
            )
        except Exception as e:
            last_qdrant_call.update(
                {"embed_text": embed_text, "payload": payload, "status": f"❌ فشل: {e}"}
            )

    # نستبدل الدالة اللي tests_service.py بينادي عليها بالـ wrapper اللي بيسجل كل حاجة
    tests_service_module.upsert_test_vector = logged_upsert_test_vector

    if args.skip_vectors:
        print("⚠️  --skip-vectors مفعّل: مش هيتعمل أي upsert فعلي لـ Qdrant دلوقتي.")

    with app.app_context():
        from sqlalchemy import inspect as sql_inspect
        columns = {column["name"] for column in sql_inspect(db.engine).get_columns("lab_services")}
        required = {"general_price_max", "general_price_min", "el_adel_price"}
        if not required.issubset(columns):
            raise RuntimeError(f"جدول lab_services ناقص أعمدة: {sorted(required - columns)}")

        lab = (db.session.get(Laboratory, args.laboratory_id)
               if args.laboratory_id is not None else get_target_laboratory())
        if lab is None:
            raise RuntimeError(f"المعمل ID={args.laboratory_id} غير موجود")
        print(f"➡️  هيتم الإدراج على المعمل: {lab.name} (id={lab.id})")

        total = len(rows)
        created, updated, skipped, failed, vector_failed = 0, 0, 0, 0, 0

        try:
            for i, row in enumerate(rows, start=1):
                name = row["name"]
                general_price_max = row["general_price_max"]
                general_price_min = row["general_price_min"]
                el_adel_price = row["el_adel_price"]

                print(f"[{i}/{total}] {name} ...", end=" ")

                try:
                    existing = LabService.query.filter_by(name=name).first()
                    if existing:
                        if not args.update_existing_prices:
                            print("⏭️ موجود بالفعل - skip")
                            skipped += 1
                            continue
                        if args.dry_run:
                            print("🔎 dry-run: تحديث الأسعار فقط (بدون تعديل)")
                            skipped += 1
                            continue
                        existing.general_price_max = general_price_max
                        existing.general_price_min = general_price_min
                        existing.el_adel_price = el_adel_price
                        db.session.commit()
                        updated += 1
                        print("✅ تم تحديث الأسعار بدون إعادة توليد AI")
                        write_detail_log(
                            args.log_file, i, total, name, None, None,
                            {"general_price_max": general_price_max,
                             "general_price_min": general_price_min,
                             "el_adel_price": el_adel_price},
                            "✅ تم تحديث أسعار تحليل موجود", None,
                            "لم يتغير: بيانات الـ embedding كما هي",
                        )
                        continue
                except Exception as exc:
                    db.session.rollback()
                    failed += 1
                    print(f"❌ خطأ أثناء فحص/تحديث تحليل موجود: {exc}")
                    log_failure(name, general_price_max, el_adel_price, str(exc))
                    continue

                generated_dict = None
                db_fields = None
                db_status = "(لسه ماتحفظش)"
                last_qdrant_call.clear()

                try:
                    # الخطوة اللي بتستخدم الـ AI (نفس زر Generate في الفورم)
                    generated = generate_test(name=name)
                    generated_dict = generation_result_to_dict(generated)

                    db_fields = {
                        "laboratory_id": lab.id,
                        "name": name,
                        "general_price_max": general_price_max,
                        "general_price_min": general_price_min,
                        "el_adel_price": el_adel_price,
                        "description": generated.description,
                        "patient_instructions": generated.patient_instructions,
                        "duration": generated.duration,
                        "sample_type": generated.sample_type,
                        "keywords": generated.keywords,
                        "alias_name": generated.alias_name,
                    }

                    if args.dry_run:
                        db_status = "(dry-run - لسه ماتحطش في الداتابيز)"
                        # بنحسب نص الـ embedding والـ payload للعرض بس من غير ما نبعت حاجة فعلي
                        embed_text = build_test_text(name, generated.description, generated.keywords)
                        last_qdrant_call.update(
                            {
                                "embed_text": embed_text,
                                "payload": {
                                    "test_id": None,
                                    "name": name,
                                    "description": generated.description,
                                    "keywords": generated.keywords or [],
                                },
                                "status": "(dry-run - لسه محصلش إرسال)",
                            }
                        )
                        print("✅ (dry-run, لسه ماتحطش في الداتابيز)")
                        created += 1
                        write_detail_log(
                            args.log_file, i, total, name, generated_dict,
                            last_qdrant_call.get("embed_text"), db_fields, db_status,
                            last_qdrant_call.get("payload"), last_qdrant_call.get("status"),
                        )
                        time.sleep(args.sleep)
                        continue

                    tests_service = TestsService(laboratory_id=lab.id)
                    new_test, message = tests_service.create_lab_service(
                        name=name,
                        general_price_max=general_price_max,
                        el_adel_price=el_adel_price,
                        description=generated.description,
                        patient_instructions=generated.patient_instructions,
                        duration=generated.duration,
                        sample_type=generated.sample_type,
                        keywords=generated.keywords,
                        alias_name=generated.alias_name,
                    )

                    if new_test:
                        created += 1
                        db_status = "✅ تم الحفظ بنجاح"
                        vector_status = last_qdrant_call.get("status", "⚠️ لم يتم استدعاء Qdrant")
                        if vector_status.startswith("❌") or vector_status.startswith("⚠️"):
                            vector_failed += 1
                            print("⚠️ تم حفظ MySQL لكن Qdrant فشل/لم يُستدعَ")
                        else:
                            print("✅ تم الإدراج" if not args.skip_vectors else "✅ تم حفظ MySQL (بدون Qdrant)")
                    else:
                        print(f"⚠️ فشل: {message}")
                        db_status = f"❌ فشل: {message}"
                        log_failure(name, general_price_max, el_adel_price, message)
                        failed += 1

                    write_detail_log(
                        args.log_file, i, total, name, generated_dict,
                        last_qdrant_call.get("embed_text"), db_fields, db_status,
                        last_qdrant_call.get("payload"), last_qdrant_call.get("status", "(لسه محصلش)"),
                    )

                except KeyboardInterrupt:
                    # نسيبها تتفلتر لبره عشان تتلقط في الـ except الخارجي وتطبع ملخص نظيف
                    raise
                except Exception as e:
                    print(f"❌ Exception: {e}")
                    log_failure(name, general_price_max, el_adel_price, str(e))
                    write_detail_log(
                        args.log_file, i, total, name, generated_dict,
                        last_qdrant_call.get("embed_text"), db_fields, f"❌ Exception: {e}",
                        last_qdrant_call.get("payload"), last_qdrant_call.get("status", "(لسه محصلش)"),
                    )
                    failed += 1

                time.sleep(args.sleep)

        except KeyboardInterrupt:
            print("\n\n⏹️  تم إيقاف الاسكريبت يدويًا (Ctrl+C).")
            print("   اللي اتعمله لحد دلوقتي محفوظ في الداتابيز - شغّل نفس الأمر تاني وهو هيكمل من نفس النقطة.")

        print("\n----------------------------------------")
        print(f"تم إنشاء: {created}")
        print(f"تم تحديث أسعار: {updated}")
        print(f"اتخطى:   {skipped}")
        print(f"فشل:     {failed}")
        if not args.skip_vectors and not args.dry_run:
            print(f"تحذيرات Qdrant: {vector_failed}")
        if failed:
            print(f"راجع التفاصيل في: {FAILED_LOG_PATH}")
        print(f"تفاصيل كل تحليل (التوليد + الـ embedding + الداتابيز + Qdrant) في: {args.log_file}")
        print("----------------------------------------")


if __name__ == "__main__":
    main()