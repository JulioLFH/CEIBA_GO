"""Ceiba GO - app de entregas de materiales (puerta a puerta, origen -> destino)."""
import os
import secrets
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# Clave propia de esta instalacion (se crea una sola vez en .env.clave)
_clave = BASE_DIR / ".env.clave"
if os.environ.get("SECRET_KEY"):
    SECRET_KEY = os.environ["SECRET_KEY"]
else:
    if not _clave.exists():
        _clave.write_text(secrets.token_urlsafe(50), encoding="utf-8")
    SECRET_KEY = _clave.read_text(encoding="utf-8").strip()

DEBUG = os.environ.get("DEBUG", "0") == "1"
ALLOWED_HOSTS = ["*"]
# Dominio publico (Cloudflare -> IIS sitio CeibaGO -> 127.0.0.1:8040). CSRF_ORIGENES agrega otros, separados por coma.
CSRF_TRUSTED_ORIGINS = ["https://go.ceiba-tecnologic.com"] + [
    o for o in os.environ.get("CSRF_ORIGENES", "").split(",") if o]
# IIS envia X-Forwarded-Proto = https cuando la visita llego por https (ProtoMap en su web.config): asi los links
# (seguimiento, guias) salen con https://
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
# el dominio real (go.ceiba-tecnologic.com) llega en X-Forwarded-Host: sin esto los links salen con 127.0.0.1:8040
USE_X_FORWARDED_HOST = True

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "envios",
    "operador",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "ceibago.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "envios.context.rol",
                "operador.context.operador",
            ],
        },
    },
]

WSGI_APPLICATION = "ceibago.wsgi.application"

# Base de datos: PostgreSQL si hay configuración (variables PG_* o el archivo .env.postgres, privado), si no SQLite.
# CEIBAGO_SQLITE=1 fuerza SQLite (lo usa la migración para leer los datos anteriores).
def _env_postgres():
    datos = {}
    ruta = BASE_DIR / ".env.postgres"
    if ruta.exists():
        for linea in ruta.read_text(encoding="utf-8-sig").splitlines():
            if linea.strip() and not linea.startswith("#") and "=" in linea:
                k, v = linea.split("=", 1)
                datos[k.strip()] = v.strip()
    return {**datos, **{k: v for k, v in os.environ.items() if k.startswith("PG_") or k.startswith("PGSSL")}}


_PG = {} if os.environ.get("CEIBAGO_SQLITE") == "1" else _env_postgres()
if _PG.get("PG_BASE"):
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": _PG["PG_BASE"],
            "USER": _PG.get("PG_USUARIO", ""),
            "PASSWORD": _PG.get("PG_CLAVE", ""),
            "HOST": _PG.get("PG_HOST", "127.0.0.1"),
            "PORT": _PG.get("PG_PUERTO", "5432"),
            "CONN_MAX_AGE": 600,
            "CONN_HEALTH_CHECKS": True,
            "OPTIONS": {k: v for k, v in {"sslmode": _PG.get("PGSSLMODE"),
                                          "sslrootcert": _PG.get("PGSSLROOTCERT")}.items() if v},
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "ceibago.sqlite3",
            "OPTIONS": {"timeout": 20},
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 6}},
]

LANGUAGE_CODE = "es-pe"
TIME_ZONE = "America/Lima"
USE_I18N = True
USE_TZ = True
USE_THOUSAND_SEPARATOR = False

STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
}
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "inicio"
LOGOUT_REDIRECT_URL = "home"
DATA_UPLOAD_MAX_MEMORY_SIZE = 15 * 1024 * 1024
