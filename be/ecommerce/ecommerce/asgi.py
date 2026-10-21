"""
ASGI config for ecommerce project.

It exposes the ASGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/5.0/howto/deployment/asgi/
"""

import os

from django.core.asgi import get_asgi_application
from ecommerce.sentry import init_sentry

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'ecommerce.settings')

# Initialise Sentry (card 5ggb4929) after DJANGO_SETTINGS_MODULE is set.
# No-op when SENTRY_DSN is unset or sentry-sdk is not installed.
init_sentry()

application = get_asgi_application()
