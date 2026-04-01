import os
from channels.routing import ProtocolTypeRouter, URLRouter
from django.core.asgi import get_asgi_application
from django.urls import path
from chat.consumers import ChatConsumer, PresenceConsumer
from chat.middleware import JwtAuthMiddleware

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'chat_project.settings')

django_asgi_app = get_asgi_application()

application = ProtocolTypeRouter({
    "http": django_asgi_app,
    "websocket": JwtAuthMiddleware(
        URLRouter([
            path("ws/presence/", PresenceConsumer.as_asgi()),
            path("ws/chat/<int:room_id>/", ChatConsumer.as_asgi()),
        ])
    ),
})
