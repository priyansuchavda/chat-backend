from rest_framework import viewsets, permissions, status, generics
from rest_framework.response import Response
from rest_framework.decorators import action
from django.db.models import Q
from django.contrib.auth.models import User
from .models import ChatRoom, Message, ChatParticipant, MessageStatus, Profile
from .serializers import (
    ChatRoomSerializer, MessageSerializer, UserSerializer, 
    RegisterSerializer, ProfileEditSerializer
)

class RegisterView(generics.CreateAPIView):
    queryset = User.objects.all()
    permission_classes = (permissions.AllowAny,)
    serializer_class = RegisterSerializer

from rest_framework import filters

class UserListView(generics.ListAPIView):
    queryset = User.objects.all()
    serializer_class = UserSerializer
    permission_classes = (permissions.IsAuthenticated,)
    filter_backends = [filters.SearchFilter]
    search_fields = ['username', 'email']

    def get_queryset(self):
        # Exclude current user from the list
        return User.objects.exclude(id=self.request.user.id)

class ProfileView(generics.RetrieveUpdateAPIView):
    serializer_class = ProfileEditSerializer
    permission_classes = (permissions.IsAuthenticated,)

    def get_object(self):
        return self.request.user.profile

class RoomViewSet(viewsets.ModelViewSet):
    serializer_class = ChatRoomSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return ChatRoom.objects.filter(participants__user=self.request.user)

    def perform_create(self, serializer):
        room = serializer.save()
        ChatParticipant.objects.create(user=self.request.user, room=room)

    @action(detail=False, methods=['post'])
    def start_private_chat(self, request):
        target_user_id = request.data.get('user_id')
        if not target_user_id:
            return Response({"error": "user_id is required"}, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            target_user = User.objects.get(id=target_user_id)
        except User.DoesNotExist:
            return Response({"error": "User does not exist"}, status=status.HTTP_404_NOT_FOUND)

        # Check if a private room already exists between these two
        existing_rooms = ChatRoom.objects.filter(
            type='private',
            participants__user=self.request.user
        ).filter(
            participants__user=target_user
        )

        if existing_rooms.exists():
            room = existing_rooms.first()
            serializer = self.get_serializer(room)
            return Response(serializer.data)

        # Create new private room
        room = ChatRoom.objects.create(type='private')
        ChatParticipant.objects.create(user=self.request.user, room=room)
        ChatParticipant.objects.create(user=target_user, room=room)
        
        serializer = self.get_serializer(room)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['get'])
    def messages(self, request, pk=None):
        room = self.get_object()
        msgs = Message.objects.filter(room=room).order_by('-created_at')[:50]
        serializer = MessageSerializer(msgs, many=True)
        return Response(serializer.data)

class MessageViewSet(viewsets.ModelViewSet):
    serializer_class = MessageSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        # Base queryset for listing: all messages in rooms where the user is a participant
        return Message.objects.filter(room__participants__user=self.request.user)

    def perform_destroy(self, instance):
        # Hard delete (or set is_deleted) only if the user is the sender
        if instance.sender == self.request.user:
            # We'll follow the model's is_deleted field for a "soft delete" first
            instance.is_deleted = True
            instance.save()
        else:
            # You could raise a permission error here
            pass

    @action(detail=False, methods=['post'])
    def bulk_delete(self, request):
        msg_ids = request.data.get('message_ids', [])
        if not msg_ids:
            return Response({"error": "message_ids is required"}, status=status.HTTP_400_BAD_REQUEST)
        
        # Only allow deleting messages SENT by the current user
        deleted_count = Message.objects.filter(
            id__in=msg_ids, 
            sender=request.user
        ).update(is_deleted=True)

        return Response({
            "message": f"Successfully deleted {deleted_count} messages.",
            "deleted_count": deleted_count
        }, status=status.HTTP_200_OK)
