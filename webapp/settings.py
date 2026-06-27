from pathlib import Path
from decouple import config
import sys

BASE_DIR = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# Suppress a spurious CPython 3.13 bug that prints a harmless SystemError
# on interpreter shutdown when daemon threads are still running.
# https://github.com/python/cpython/issues/116786
# ---------------------------------------------------------------------------
_original_unraisablehook = sys.unraisablehook

def _unraisablehook(unraisable):
    if (
        unraisable.exc_type is SystemError
        and "is_done" in str(unraisable.exc_value)
    ):
        return  # swallow the 3.13 threading-shutdown bug
    _original_unraisablehook(unraisable)

sys.unraisablehook = _unraisablehook

SECRET_KEY = config('SECRET_KEY', default='django-insecure-r3c0rd3r-s3cr3t-k3y-ch4ng3-in-pr0duct10n')

DEBUG = config('DEBUG', default=True, cast=bool)

ALLOWED_HOSTS = ['*']

CSRF_TRUSTED_ORIGINS = [
    'http://localhost:8000',
    'http://127.0.0.1:8000',
    'http://localhost:8001',
    'http://127.0.0.1:8001',
    'http://localhost',
    'http://127.0.0.1',
]

# Maximum number of headless replay threads — resolved from app_config DB below.
MAX_PARALLEL_REPLAYS = 4

# Maximum workers in the global replay ThreadPoolExecutor.
# Also read from app_config("replay.max_parallel_replays") at first use.
REPLAY_MAX_WORKERS = 8

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'recorder.apps.RecorderConfig',
    'api_testcases.apps.ApiTestcasesConfig',
    'db_testcases.apps.DbTestcasesConfig',
    'django_extensions',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'recorder.middleware.TenantMiddleware',          # resolves request.tenant_id
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'webapp.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [
            BASE_DIR / 'templates',
            BASE_DIR / 'templates_api',
            BASE_DIR / 'templates_db',
        ],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'recorder.context_processors.app_features',
                'api_testcases.context_processors.app_theme_settings',
                'db_testcases.context_processors.app_navigation',
            ],
        },
    },
]

WSGI_APPLICATION = 'webapp.wsgi.application'

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': config('DB_NAME', default='automation_db'),
        'USER': config('DB_USER', default='postgres'),
        'PASSWORD': config('DB_PASSWORD', default='password'),
        'HOST': config('DB_HOST', default='localhost'),
        'PORT': config('DB_PORT', default='5432'),
        # Keep connections alive across requests (Django-native connection pool).
        # Each worker thread reuses its own persistent connection instead of
        # opening + closing one per request.
        'CONN_MAX_AGE': 60,          # seconds; None = unlimited lifetime
        'CONN_HEALTH_CHECKS': True,  # re-check before reuse (avoids stale conn errors)
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# PBKDF2 for new passwords; BCrypt enabled so Django can verify legacy hashed passwords.
PASSWORD_HASHERS = [
    'django.contrib.auth.hashers.PBKDF2PasswordHasher',
    'django.contrib.auth.hashers.BCryptPasswordHasher',
]

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

STATIC_URL = '/static/'
STATICFILES_DIRS = [
    BASE_DIR / 'static',
    BASE_DIR / 'static_api',
    BASE_DIR / 'static_db',
]
STATIC_ROOT = BASE_DIR / 'staticfiles'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Silence the Django 5.x system check about the default hashing algorithm
DEFAULT_HASHING_ALGORITHM = 'sha256'

LOGIN_URL = '/login/'
LOGIN_REDIRECT_URL = '/dashboard/'
LOGOUT_REDIRECT_URL = '/login/'

# When running multiple instances on the same host (e.g. :8000 and :8001),
# browsers share cookies by hostname, so both servers would overwrite each
# other's "sessionid" cookie.  Unique names are derived automatically from
# the PORT env var (set by run.bat), or can be overridden explicitly:
#   SESSION_COOKIE_NAME=sessionid_8000  CSRF_COOKIE_NAME=csrftoken_8000
import os as _os, sys as _sys

def _server_port() -> str:
    """Best-effort detection of the port this instance is serving on."""
    # 1. Already-set explicit env var (highest priority)
    if _os.environ.get('SESSION_COOKIE_NAME'):
        return ''          # caller won't use this; explicit name wins
    # 2. PORT env var – set by run.bat before launching manage.py
    _p = _os.environ.get('PORT', '')
    if _p.isdigit():
        return _p
    # 3. Parse "runserver [ADDR:]PORT" from sys.argv
    for _a in _sys.argv:
        _part = _a.split(':')[-1]
        if _part.isdigit() and 1024 <= int(_part) <= 65535:
            return _part
    return '8000'

_port_suffix = _server_port()
SESSION_COOKIE_NAME = config('SESSION_COOKIE_NAME',
                              default=f'sessionid_{_port_suffix}' if _port_suffix else 'sessionid')
CSRF_COOKIE_NAME    = config('CSRF_COOKIE_NAME',
                              default=f'csrftoken_{_port_suffix}'  if _port_suffix else 'csrftoken')

# ---------------------------------------------------------------------------
# Auto-create the target PostgreSQL database if it does not yet exist.
# This runs once at Django startup (settings load), before any connection
# to "automation_db" is attempted.
# ---------------------------------------------------------------------------
def _ensure_db_exists():
    import psycopg2
    from psycopg2 import sql as _sql

    _cfg = DATABASES["default"]
    _db   = _cfg["NAME"]
    _user = _cfg["USER"]
    _pw   = _cfg["PASSWORD"]
    _host = _cfg["HOST"]
    _port = _cfg["PORT"]

    for _maint in ("postgres", "template1"):
        _conn = None
        try:
            _conn = psycopg2.connect(
                dbname=_maint, user=_user, password=_pw, host=_host, port=_port
            )
            _conn.autocommit = True
            with _conn.cursor() as _cur:
                _cur.execute("SELECT 1 FROM pg_database WHERE datname = %s;", (_db,))
                if _cur.fetchone() is None:
                    _cur.execute(_sql.SQL("CREATE DATABASE {}").format(_sql.Identifier(_db)))
            break
        except Exception:
            if _maint == "template1":
                pass   # silently skip if postgres is unreachable entirely
        finally:
            if _conn is not None:
                try:
                    _conn.close()
                except Exception:
                    pass

_ensure_db_exists()


def _read_max_parallel_replays(default: int = 4) -> int:
    """Query app_config for replay.max_parallel_replays at startup (runs after DATABASES is defined)."""
    _cfg = DATABASES["default"]
    try:
        import psycopg2
        _conn = psycopg2.connect(
            dbname=_cfg["NAME"], user=_cfg["USER"], password=_cfg["PASSWORD"],
            host=_cfg["HOST"], port=_cfg["PORT"],
        )
        try:
            with _conn.cursor() as _cur:
                _cur.execute(
                    "SELECT value FROM app_config WHERE key = 'replay.max_parallel_replays'",
                )
                row = _cur.fetchone()
                if row:
                    return max(1, int(row[0]))
        finally:
            _conn.close()
    except Exception:
        pass
    return default


MAX_PARALLEL_REPLAYS = _read_max_parallel_replays(MAX_PARALLEL_REPLAYS)
