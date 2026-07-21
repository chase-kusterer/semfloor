"""WSGI entry point (synchronous servers: gunicorn, etc.)."""
import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "semfloor.settings")
application = get_wsgi_application()
