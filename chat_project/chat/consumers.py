import asyncio
import json
import threading
import time

from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from .models import ChatRoom, Message, ChatParticipant, MessageStatus, Profile

# If no ping (or any frame) arrives for this long, treat presence as dead and broadcast offline.
# Flutter should send {"type": "ping"} at least every ~25s (see presence_service.dart).
PRESENCE_STALE_AFTER_S = 75
PRESENCE_STALE_POLL_S = 15


class PresenceConsumer(AsyncWebsocketConsumer):
    """
    Global presence socket. Flutter connects to this on app startup.
    App open = online. App closed/backgrounded = offline.
    URL: ws://<domain>/ws/presence/?token=JWT
    """

    async def connect(self):
        self.user = self.scope["user"]

        if not self.user.is_authenticated:
            await self.close(code=4001)
            return

        # Join a global presence group so we can broadcast status
        self.presence_group = "global_presence"
        await self.channel_layer.group_add(
            self.presence_group,
            self.channel_name
        )

        await self.set_online_status(True)

        # Broadcast to everyone that this user is now online
        await self.channel_layer.group_send(
            self.presence_group,
            {
                "type": "presence_update",
                "user_id": self.user.id,
                "is_online": True
            }
        )

        await self.accept()
        self._touch_presence_activity()
        self._stale_poll_task = asyncio.create_task(self._poll_presence_staleness())

    def _touch_presence_activity(self):
        self._last_presence_activity = time.monotonic()

    async def _poll_presence_staleness(self):
        try:
            while True:
                await asyncio.sleep(PRESENCE_STALE_POLL_S)
                if time.monotonic() - self._last_presence_activity > PRESENCE_STALE_AFTER_S:
                    await self._close_stale_presence()
                    return
        except asyncio.CancelledError:
            return

    async def _close_stale_presence(self):
        """No heartbeat: clear DB flag and notify others, then close this socket."""
        if getattr(self, "_stale_presence_closed", False):
            return
        self._stale_presence_closed = True

        if hasattr(self, "user") and self.user.is_authenticated:
            await self.set_online_status(False)
            await self.channel_layer.group_send(
                self.presence_group,
                {
                    "type": "presence_update",
                    "user_id": self.user.id,
                    "is_online": False,
                },
            )
        await self.close(code=4000)

    async def disconnect(self, close_code):
        task = getattr(self, "_stale_poll_task", None)
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        already_offline = getattr(self, "_stale_presence_closed", False)
        if (
            hasattr(self, "user")
            and self.user.is_authenticated
            and not already_offline
        ):
            await self.set_online_status(False)

            await self.channel_layer.group_send(
                self.presence_group,
                {
                    "type": "presence_update",
                    "user_id": self.user.id,
                    "is_online": False,
                },
            )

        if hasattr(self, 'presence_group'):
            await self.channel_layer.group_discard(
                self.presence_group,
                self.channel_name
            )

    async def receive(self, text_data):
        self._touch_presence_activity()
        try:
            data = json.loads(text_data)
            if data.get("type") == "ping":
                await self.send(text_data=json.dumps({"type": "pong"}))
        except json.JSONDecodeError:
            pass

    async def presence_update(self, event):
        await self.send(text_data=json.dumps({
            "type": "status_update",
            "user_id": event["user_id"],
            "is_online": event["is_online"]
        }))

    @database_sync_to_async
    def set_online_status(self, status):
        profile = Profile.objects.filter(user=self.user).first()
        if profile:
            profile.is_online = status
            profile.save()


class ChatConsumer(AsyncWebsocketConsumer):
    """
    Per-room chat socket. Only handles messages.
    URL: ws://<domain>/ws/chat/<room_id>/?token=JWT
    """

    async def connect(self):
        self.room_id = self.scope['url_route']['kwargs']['room_id']
        self.room_group_name = f"chat_{self.room_id}"
        self.user = self.scope["user"]

        if not self.user.is_authenticated:
            await self.close(code=4001)
            return

        if not await self.is_member(self.user, self.room_id):
            await self.close(code=4003)
            return

        await self.channel_layer.group_add(
            self.room_group_name,
            self.channel_name
        )
        await self.accept()

    async def disconnect(self, close_code):
        if hasattr(self, 'room_group_name'):
            await self.channel_layer.group_discard(
                self.room_group_name,
                self.channel_name
            )

    async def receive(self, text_data):
        try:
            data = json.loads(text_data)
            message_content = data.get("content")
            msg_type = data.get("message_type", "text")

            msg = await self.save_message(self.user, self.room_id, message_content, msg_type)

            # Push notification for recipients whose app is closed (non-blocking)
            threading.Thread(
                target=self._send_push_notifications,
                args=(msg.id, self.room_id, message_content),
                daemon=True,
            ).start()

            await self.channel_layer.group_send(
                self.room_group_name,
                {
                    "type": "chat_message",
                    "id": msg.id,
                    "sender_id": self.user.id,
                    "sender_name": self.user.username,
                    "content": msg.content,
                    "message_type": msg.message_type,
                    "created_at": str(msg.created_at)
                }
            )
        except Exception as e:
            await self.send(text_data=json.dumps({"error": str(e)}))

    async def chat_message(self, event):
        data = event.copy()
        data.pop("type")
        await self.send(text_data=json.dumps(data))

    @database_sync_to_async
    def is_member(self, user, room_id):
        return ChatParticipant.objects.filter(user=user, room_id=room_id).exists()

    @database_sync_to_async
    def save_message(self, user, room_id, content, msg_type):
        room = ChatRoom.objects.get(id=room_id)
        msg = Message.objects.create(
            room=room,
            sender=user,
            content=content,
            message_type=msg_type
        )
        return msg

    def _send_push_notifications(self, message_id, room_id, content):
        """Run in background thread so WebSocket is not blocked."""
        from .firebase_service import notify_room_participants
        from .models import ChatRoom

        try:
            room = ChatRoom.objects.get(id=room_id)
            notify_room_participants(self.user, room, content, message_id=message_id)
        except Exception as e:
            import logging
            logging.getLogger(__name__).error("Push notification error: %s", e)
