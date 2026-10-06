"""Application configuration for Iya Ashabi Bukka Limited POS.

The system is LOCAL-FIRST: it never requires an internet connection.
All mutable business data lives in a Windows application-data directory
so the installation folder itself never needs to be writable.
"""
from __future__ import annotations

import os
import secrets
import sys
from pathlib import Path

from dotenv import load_dotenv

# Base directory of the project / frozen executable
BASE_DIR = Path(__file__).resolve().parent
if getattr(sys, "frozen", False):
    # PyInstaller unpacks the code into a sub-folder, so the folder that
    # actually holds the .exe is the application root. That is where an
    # installer would drop an optional .env file.
    APP_ROOT = Path(sys.executable).resolve().parent
else:
    APP_ROOT = BASE_DIR.parent


def _env_path(value: str, fallback: Path) -> Path:
    p = Path(value)
    if p.is_absolute():
        return p
    return (APP_ROOT / p).resolve()


def app_data_dir() -> Path:
    """Resolve the application data directory (database, logs, backups).

    Windows  -> %PROGRAMDATA%\\IyaAshabiBukkaPOS
    Other OS -> ~/.iya_ashabi_bukka_pos
    """
    override = os.environ.get("IAB_DATA_DIR")
    if override:
        return Path(override).resolve()
    if os.name == "nt":
        program_data = os.environ.get("PROGRAMDATA") or str(Path.home())
        return Path(program_data) / "IyaAshabiBukkaPOS"
    return Path.home() / ".iya_ashabi_bukka_pos"


DATA_DIR = app_data_dir()
UPLOAD_DIR = DATA_DIR / "uploads"
BACKUP_DIR = _env_path(
    os.environ.get("BACKUP_DIRECTORY", str(DATA_DIR / "backups")),
    DATA_DIR / "backups",
)
LOG_DIR = DATA_DIR / "logs"


def ensure_dirs() -> None:
    for directory in (DATA_DIR, UPLOAD_DIR, BACKUP_DIR, LOG_DIR):
        directory.mkdir(parents=True, exist_ok=True)


def default_database_uri() -> str:
    configured = os.environ.get("DATABASE_PATH")
    if configured:
        path = _env_path(configured, DATA_DIR / "pos.db")
    else:
        path = DATA_DIR / "pos.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{path.as_posix()}"


def _secret_key() -> str:
    key = os.environ.get("SECRET_KEY")
    if key and key != "change-me-to-a-long-random-string":
        return key
    # Persist a generated key so sessions survive restarts.
    key_file = DATA_DIR / "secret.key"
    if key_file.exists():
        return key_file.read_text(encoding="utf-8").strip()
    generated = secrets.token_hex(32)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    key_file.write_text(generated, encoding="utf-8")
    try:
        os.chmod(key_file, 0o600)
    except OSError:
        pass
    return generated


class BaseConfig:
    """Settings shared by every environment."""

    ENV_NAME = "base"
    DEBUG = False
    TESTING = False

    SECRET_KEY = _secret_key()
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    # Secure cookies require HTTPS. A local offline POS serves plain HTTP on
    # localhost, so this stays configurable and is off by default.
    SESSION_COOKIE_SECURE = os.environ.get("SESSION_COOKIE_SECURE", "0") == "1"
    PERMANENT_SESSION_LIFETIME = 60 * 60 * 12

    SQLALCHEMY_DATABASE_URI = default_database_uri()
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True,
        "connect_args": {"check_same_thread": False, "timeout": 30},
    }

    DATA_DIR = DATA_DIR
    UPLOAD_DIR = UPLOAD_DIR
    BACKUP_DIR = BACKUP_DIR
    LOG_DIR = LOG_DIR

    APP_HOST = os.environ.get("APP_HOST", "127.0.0.1")
    APP_PORT = int(os.environ.get("APP_PORT", "5000"))

    PRINTER_NAME = os.environ.get("PRINTER_NAME", "")
    PRINTER_WIDTH_MM = int(os.environ.get("PRINTER_WIDTH_MM", "80"))
    MOCK_HARDWARE = os.environ.get("MOCK_HARDWARE", "0") == "1"
    CUSTOMER_DISPLAY_ENABLED = (
        os.environ.get("CUSTOMER_DISPLAY_ENABLED", "1") == "1"
    )

    AUTO_BACKUP_ENABLED = os.environ.get("AUTO_BACKUP_ENABLED", "1") == "1"
    BACKUP_RETENTION_COUNT = int(os.environ.get("BACKUP_RETENTION_COUNT", "30"))

    SESSION_TIMEOUT_MINUTES = int(os.environ.get("SESSION_TIMEOUT_MINUTES", "30"))
    LOGIN_MAX_ATTEMPTS = int(os.environ.get("LOGIN_MAX_ATTEMPTS", "5"))
    LOGIN_LOCKOUT_MINUTES = int(os.environ.get("LOGIN_LOCKOUT_MINUTES", "15"))

    MAX_CONTENT_LENGTH = 8 * 1024 * 1024
    ALLOWED_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "bmp", "webp"}
    SETUP_COMPLETED = False


class DevelopmentConfig(BaseConfig):
    ENV_NAME = "development"
    DEBUG = True
    TEMPLATES_AUTO_RELOAD = True


class ProductionConfig(BaseConfig):
    ENV_NAME = "production"
    DEBUG = False
    TESTING = False
    SESSION_COOKIE_SECURE = os.environ.get("SESSION_COOKIE_SECURE", "0") == "1"


class TestingConfig(BaseConfig):
    ENV_NAME = "testing"
    TESTING = True
    DEBUG = False
    SQLALCHEMY_DATABASE_URI = f"sqlite:///{(DATA_DIR / 'test-pos.db').as_posix()}"
    WTF_CSRF_ENABLED = False
    MOCK_HARDWARE = True
    AUTO_BACKUP_ENABLED = False
    SESSION_TIMEOUT_MINUTES = 60


CONFIGS = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "testing": TestingConfig,
}


def get_config(name: str | None = None):
    if name is None:
        name = os.environ.get("APP_ENV", "development")
    return CONFIGS.get(name, DevelopmentConfig)


def load_environment() -> None:
    env_file = APP_ROOT / ".env"
    if env_file.exists():
        load_dotenv(env_file, override=False)
