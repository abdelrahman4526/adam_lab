from flask import Flask
from flask_login import LoginManager
from flask_migrate import Migrate

from config import Config
from models.models import db, User
from routes.login_routes import main_bp
from routes.user_routes import users_bp
from routes.platform_routes import platforms_bp
from routes.subscription_routes import subscription_bp
from routes.test_routes import test_bp
from routes.laboratory_routes import laboratory_bp
from routes.booking_routes import bookings_bp
from routes.complaint_routes import complaints_bp
from routes.feedback_routes import feedbacks_bp
from routes.inquiry_routes import inquiries_bp
from routes.page_routes import pages_bp
from routes.gmail_routes import gmail_auth_bp
from routes.webhook_routes import webhook_bp
from routes.bundle_routes import bundle_bp
from routes.health_routes import health_bp
from routes.public_routes import public_bp
from routes.result_routes import results_bp

import logging
from logging.handlers import RotatingFileHandler
import os


import logging
import os
import sys
from logging.handlers import RotatingFileHandler


def setup_app_logging(app):
    # 1. Logging level
    level_name = str(
        app.config.get("LOG_LEVEL", "INFO")
    ).upper()

    log_level = getattr(
        logging,
        level_name,
        logging.INFO
    )

    # 2. Stable log directory
    base_dir = os.path.dirname(
        os.path.abspath(__file__)
    )

    log_dir = os.environ.get(
        "LOG_DIR",
        os.path.join(base_dir, "logs")
    )

    os.makedirs(log_dir, exist_ok=True)

    log_file = os.path.abspath(
        os.path.join(log_dir, "adam-lab.log")
    )

    # 3. Log format
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] "
        "[PID:%(process)d] %(name)s: %(message)s"
    )

    # 4. Root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # 5. File handler
    existing_file_handler = next(
        (
            h for h in root_logger.handlers
            if isinstance(h, RotatingFileHandler)
            and h.baseFilename == log_file
        ),
        None
    )

    if existing_file_handler is None:
        file_handler = RotatingFileHandler(
            filename=log_file,
            maxBytes=20 * 1024 * 1024,
            backupCount=10,
            encoding="utf-8"
        )

        file_handler.setFormatter(formatter)
        file_handler.setLevel(log_level)

        root_logger.addHandler(file_handler)

    else:
        existing_file_handler.setLevel(log_level)

    # 6. Console handler for Docker logs
    has_console_handler = any(
        isinstance(h, logging.StreamHandler)
        and not isinstance(h, logging.FileHandler)
        for h in root_logger.handlers
    )

    if not has_console_handler:
        console_handler = logging.StreamHandler(
            sys.stderr
        )

        console_handler.setFormatter(formatter)
        console_handler.setLevel(log_level)

        root_logger.addHandler(console_handler)

    # 7. Flask logger propagation
    app.logger.setLevel(log_level)
    app.logger.propagate = True

    # 8. Verify logging initialization
    root_logger.warning(
        "[LOGGING INITIALIZED] "
        "Log file: %s | Level: %s",
        log_file,
        level_name
    )

    app.logger.info(
        "[FLASK] File logging configured successfully"
    )

    return log_file

def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)
    setup_app_logging(app)

    # تأكيد وجود secret_key للـ Sessions (لو مش محدد في Config)
    if not app.config.get("SECRET_KEY"):
        app.config["SECRET_KEY"] = "adam-secret-key-medical-lab-2026"

    # Initialize extensions
    db.init_app(app)
    from utils.timeutils import fmt_local
    app.add_template_filter(lambda dt, fmt="%Y-%m-%d %H:%M": fmt_local(dt, fmt, default="—"), "local_dt")

    Migrate(app, db)  

    login_manager = LoginManager()
    login_manager.login_view = "main.login"
    login_manager.login_message = "يرجى تسجيل الدخول للوصول إلى هذه الصفحة"
    login_manager.login_message_category = "warning"
    login_manager.init_app(app)

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(User, int(user_id))

    # Register blueprints
    app.register_blueprint(main_bp)
    app.register_blueprint(users_bp)
    app.register_blueprint(platforms_bp)
    app.register_blueprint(subscription_bp)
    app.register_blueprint(test_bp)
    app.register_blueprint(laboratory_bp)
    app.register_blueprint(bookings_bp)
    app.register_blueprint(complaints_bp)
    app.register_blueprint(feedbacks_bp)
    app.register_blueprint(inquiries_bp)
    app.register_blueprint(pages_bp)
    app.register_blueprint(gmail_auth_bp)
    app.register_blueprint(webhook_bp)
    app.register_blueprint(bundle_bp)
    app.register_blueprint(health_bp)
    app.register_blueprint(public_bp)
    app.register_blueprint(results_bp)

    # Context processor for sidebar badges and layout counters
    @app.context_processor
    def inject_global_counts():
        try:
            from services.domain.inquiry_service import InquiryService
            count = InquiryService.get_pending_inquiries_count()
        except Exception:
            count = 0
        return dict(pending_inquiries_count=count)

    return app


app = create_app()

if __name__ == "__main__":
    app.run(debug=True, port=5000)