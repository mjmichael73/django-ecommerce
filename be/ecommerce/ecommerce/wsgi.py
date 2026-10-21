"""
WSGI config for ecommerce project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/5.0/howto/deployment/wsgi/
"""

import os

from django.core.wsgi import get_wsgi_application
from ecommerce.sentry import init_sentry

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'ecommerce.settings')

# Initialise Sentry (card 5ggb4929) after DJANGO_SETTINGS_MODULE is set and
# before the WSGI app is constructed — so Django request handling is covered.
# No-op when SENTRY_DSN is unset or sentry-sdk is not installed.
init_sentry()

application = get_wsgi_application()
