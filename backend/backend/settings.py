import os
from datetime import timedelta
from pathlib import Path
from dotenv import load_dotenv
# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / '.env')

# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/6.1/howto/deployment/checklist/

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = os.getenv('SECRET_KEY', 'dev-only-insecure-key')

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = os.getenv('DEBUG', 'True') == 'True'

ALLOWED_HOSTS = os.getenv('ALLOWED_HOSTS', 'localhost,127.0.0.1').split(',')


# Application definition

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'rest_framework',
    'rest_framework_simplejwt.token_blacklist',
    'corsheaders',
    'web',
]

MIDDLEWARE = [
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'backend.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'backend.wsgi.application'


# Database
# https://docs.djangoproject.com/en/6.1/ref/settings/#databases

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}


# Password validation
# https://docs.djangoproject.com/en/6.1/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]


# Internationalization
# https://docs.djangoproject.com/en/6.1/topics/i18n/

LANGUAGE_CODE = 'en-us'

TIME_ZONE = 'UTC'

USE_I18N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/6.1/howto/static-files/

STATIC_URL = 'static/'


# Email
# https://docs.djangoproject.com/en/6.1/topics/email/#topic-email-configuration

# --- Email ---
# Console backend prints emails in the terminal. Set EMAIL_BACKEND in .env for real email.
EMAIL_BACKEND = os.getenv('EMAIL_BACKEND', 'django.core.mail.backends.console.EmailBackend')
DEFAULT_FROM_EMAIL = os.getenv('DEFAULT_FROM_EMAIL', 'Al Ethijahath <no-reply@localhost>')

# --- Uploaded files (private: no MEDIA_URL, so nothing is served publicly) ---
MEDIA_ROOT = BASE_DIR / 'media'

AUTH_USER_MODEL = 'web.User'

# --- API ---
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': (
        'accounts.authentication.PortalAuthentication',
    ),
    'DEFAULT_PERMISSION_CLASSES': (
        'rest_framework.permissions.IsAuthenticated',
    ),
    'EXCEPTION_HANDLER': 'accounts.utils.api_exception_handler',
    'DEFAULT_THROTTLE_RATES': {'login': '10/min', 'register': '5/hour'},
}

SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(minutes=30),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=7),
    'CHECK_REVOKE_TOKEN': True,
    'ROTATE_REFRESH_TOKENS': True,
    'BLACKLIST_AFTER_ROTATION': True,
}

# React dev servers (add the real website address before launch)
CORS_ALLOWED_ORIGINS = os.getenv(
    'CORS_ALLOWED_ORIGINS',
    'http://localhost:5173,http://localhost:3000',
).split(',')

EMAIL_HOST = os.getenv("EMAIL_HOST", "localhost")
EMAIL_PORT = int(os.getenv("EMAIL_PORT", "25"))
EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = os.getenv("EMAIL_USE_TLS", "").lower() == "true"
EMAIL_USE_SSL = os.getenv("EMAIL_USE_SSL", "").lower() == "true"
EMAIL_TIMEOUT = 15
CSRF_TRUSTED_ORIGINS = [v.strip() for v in os.getenv("CSRF_TRUSTED_ORIGINS", "").split(",") if v.strip()]
if not DEBUG and SECRET_KEY == "dev-only-insecure-key":
    from django.core.exceptions import ImproperlyConfigured
    raise ImproperlyConfigured("Set a private SECRET_KEY when DEBUG is false.")
if os.getenv("POSTGRES_DB") or os.getenv("DB_NAME"):
    DATABASES["default"] = {"ENGINE": "django.db.backends.postgresql",
        "NAME": os.getenv("POSTGRES_DB") or os.environ["DB_NAME"],
        "USER": os.getenv("POSTGRES_USER") or os.getenv("DB_USER", ""),
        "PASSWORD": os.getenv("POSTGRES_PASSWORD") or os.getenv("DB_PASSWORD", ""),
        "HOST": os.getenv("POSTGRES_HOST") or os.getenv("DB_HOST", ""),
        "PORT": os.getenv("POSTGRES_PORT") or os.getenv("DB_PORT", "5432")}
else:
    DATABASES["default"]["OPTIONS"] = {"transaction_mode": "IMMEDIATE", "timeout": 15}
if os.getenv("R2_PRIVATE_BUCKET"):
    STORAGES = {
        "default": {"BACKEND": "storages.backends.s3.S3Storage", "OPTIONS": {
            "bucket_name": os.environ["R2_PRIVATE_BUCKET"], "endpoint_url": os.environ["R2_ENDPOINT_URL"],
            "access_key": os.environ["R2_ACCESS_KEY_ID"], "secret_key": os.environ["R2_SECRET_ACCESS_KEY"],
            "region_name": "auto", "signature_version": "s3v4", "default_acl": None, "file_overwrite": False,
            "custom_domain": None, "querystring_auth": True, "querystring_expire": 60,
        }},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }
