from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from .models import ChatRoom, Message, ChatParticipant, MessageStatus, Profile
import json


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

    async def disconnect(self, close_code):
        if hasattr(self, 'user') and self.user.is_authenticated:
            await self.set_online_status(False)

            await self.channel_layer.group_send(
                self.presence_group,
                {
                    "type": "presence_update",
                    "user_id": self.user.id,
                    "is_online": False
                }
            )

        if hasattr(self, 'presence_group'):
            await self.channel_layer.group_discard(
                self.presence_group,
                self.channel_name
            )

    async def receive(self, text_data):
        # Client can send a heartbeat/ping if needed
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
