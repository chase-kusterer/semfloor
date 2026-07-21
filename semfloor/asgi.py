"""
ASGI entry point.

Phase 1 exposes a plain Django ASGI app. In the live-ticker sprint this becomes a
Channels ProtocolTypeRouter, e.g.:

    from channels.routing import ProtocolTypeRouter, URLRouter
    application = ProtocolTypeRouter({
        "http": django_asgi_app,
        "websocket": AuthMiddlewareStack(URLRouter(game.routing.websocket_urlpatterns)),
    })

Keeping the entry point here now means that switch is additive, not a rename.
"""
import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "semfloor.settings")
application = get_asgi_application()
