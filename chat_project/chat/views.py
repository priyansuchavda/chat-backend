from rest_framework import viewsets, permissions, status, generics
from rest_framework.response import Response
from rest_framework.decorators import action
from django.db.models import Q
from django.contrib.auth.models import User
from .models import ChatRoom, Message, ChatParticipant, MessageStatus, Profile, ConnectionRequest, DeviceToken
from .serializers import (
    ChatRoomSerializer, MessageSerializer, UserSerializer, 
    RegisterSerializer, ProfileEditSerializer, ConnectionRequestSerializer, DeviceTokenSerializer
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

class UserDetailView(generics.RetrieveAPIView):
    queryset = User.objects.all()
    serializer_class = UserSerializer
    permission_classes = (permissions.IsAuthenticated,)

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

        # Check connection status
        connection = ConnectionRequest.objects.filter(
            Q(sender=self.request.user, receiver=target_user) |
            Q(sender=target_user, receiver=self.request.user)
        ).first()

        if not connection or connection.status != 'accepted':
            return Response({"error": "Connect First. You must be connected to start a chat."}, status=status.HTTP_403_FORBIDDEN)

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

    def perform_create(self, serializer):
        message = serializer.save(sender=self.request.user)
        from .firebase_service import notify_room_participants
        notify_room_participants(
            self.request.user, message.room, message.content, message_id=message.id
        )

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

class ConnectionRequestViewSet(viewsets.ModelViewSet):
    serializer_class = ConnectionRequestSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        req_type = self.request.query_params.get('type')
        if req_type == 'incoming':
            return ConnectionRequest.objects.filter(
                receiver=self.request.user,
                status='pending'
            )
        
        # Users can see requests they sent or received
        return ConnectionRequest.objects.filter(
            Q(sender=self.request.user) | Q(receiver=self.request.user)
        )

    @action(detail=False, methods=['get'])
    def incoming(self, request):
        incoming_requests = ConnectionRequest.objects.filter(
            receiver=request.user,
            status='pending'
        )
        serializer = self.get_serializer(incoming_requests, many=True)
        return Response(serializer.data)

    def perform_create(self, serializer):
        receiver = serializer.validated_data['receiver']
        if receiver == self.request.user:
            raise serializers.ValidationError({"error": "You cannot send a connection request to yourself."})
        
        # Check if a request already exists
        existing_request = ConnectionRequest.objects.filter(
            Q(sender=self.request.user, receiver=receiver) | 
            Q(sender=receiver, receiver=self.request.user)
        ).first()

        if existing_request:
            if existing_request.status == 'pending':
                raise serializers.ValidationError({"error": "A connection request is already pending between these users."})
            elif existing_request.status == 'accepted':
                raise serializers.ValidationError({"error": "You are already connected with this user."})
            elif existing_request.status == 'blocked':
                raise serializers.ValidationError({"error": "Cannot send connection request. User is blocked."})
            # If rejected, maybe allow resending by updating the existing one or just reject again.
            # Here we might update the existing request to pending if the current user sends it again
            # To keep it simple, if there's an existing request, we can just update its status if it's rejected
            
            # Since perform_create creates a new one, if it exists and is rejected, we shouldn't create a new one. 
            # We'd have to handle it in create() or here.
            # Let's raise an error and tell them to use update, or we can just delete the old one and create a new one.
            existing_request.delete()

        serializer.save(sender=self.request.user, status='pending')

    @action(detail=True, methods=['post'])
    def accept(self, request, pk=None):
        connection_request = self.get_object()
        if connection_request.receiver != request.user:
            return Response({"error": "You can only accept requests sent to you."}, status=status.HTTP_403_FORBIDDEN)
        
        if connection_request.status != 'pending':
            return Response({"error": "This request is not pending."}, status=status.HTTP_400_BAD_REQUEST)
        
        connection_request.status = 'accepted'
        connection_request.save()
        return Response({"message": "Connection request accepted.", "status": "accepted"})

    @action(detail=True, methods=['post'])
    def reject(self, request, pk=None):
        connection_request = self.get_object()
        if connection_request.receiver != request.user:
            return Response({"error": "You can only reject requests sent to you."}, status=status.HTTP_403_FORBIDDEN)
        
        if connection_request.status != 'pending':
            return Response({"error": "This request is not pending."}, status=status.HTTP_400_BAD_REQUEST)
        
        connection_request.status = 'rejected'
        connection_request.save()
        return Response({"message": "Connection request rejected.", "status": "rejected"})

class DeviceTokenViewSet(viewsets.ModelViewSet):
    """
    API endpoint for managing device tokens for push notifications.
    - POST /api/device-tokens/ - Register a new device token
    - PUT /api/device-tokens/{id}/ - Update device token
    - DELETE /api/device-tokens/{id}/ - Remove device token
    """
    serializer_class = DeviceTokenSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return DeviceToken.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        # Check if token already exists for another user and remove it
        token = serializer.validated_data.get('token')
        existing_token = DeviceToken.objects.filter(token=token).exclude(user=self.request.user).first()
        if existing_token:
            existing_token.delete()
        
        serializer.save(user=self.request.user)

    @action(detail=False, methods=['post'])
    def update_or_create(self, request):
        """
        Update existing device token or create a new one.
        If token already exists for this user, update it; otherwise create new.
        """
        token = request.data.get('token')
        device_name = request.data.get('device_name', 'Unknown Device')

        if not token:
            return Response({"error": "token is required"}, status=status.HTTP_400_BAD_REQUEST)

        # Check if token already exists for another user
        existing_other_user = DeviceToken.objects.filter(token=token).exclude(user=request.user).first()
        if existing_other_user:
            existing_other_user.delete()

        device_token, created = DeviceToken.objects.update_or_create(
            token=token,
            user=request.user,
            defaults={'device_name': device_name, 'is_active': True}
        )

        serializer = self.get_serializer(device_token)
        return Response(
            {
                "message": "Device token registered successfully" if created else "Device token updated",
                "data": serializer.data
            },
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK
        )
