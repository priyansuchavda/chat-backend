from rest_framework import serializers
from django.contrib.auth.models import User
from .models import ChatRoom, Message, ChatParticipant, MessageStatus, Profile

class ProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = Profile
        fields = ['avatar', 'gender', 'dob', 'is_online', 'last_seen']
        read_only_fields = ['is_online', 'last_seen']

class UserSerializer(serializers.ModelSerializer):
    profile = ProfileSerializer(read_only=True)
    class Meta:
        model = User
        fields = ['id', 'username', 'email', 'profile']

class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, required=True, min_length=8)
    email = serializers.EmailField(required=True)

    class Meta:
        model = User
        fields = ['username', 'email', 'password']

    def validate_email(self, value):
        if User.objects.filter(email=value).exists():
            raise serializers.ValidationError("A user with this email already exists.")
        return value

    def create(self, validated_data):
        user = User.objects.create_user(
            username=validated_data['username'],
            email=validated_data['email'],
            password=validated_data['password']
        )
        return user

class ProfileEditSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source='user.username', read_only=True)
    email = serializers.EmailField(source='user.email')
    
    class Meta:
        model = Profile
        fields = ['username', 'email', 'avatar', 'gender', 'dob']

    def update(self, instance, validated_data):
        user_data = validated_data.pop('user', {})
        email = user_data.get('email')
        
        if email:
            instance.user.email = email
            instance.user.save()
            
        return super().update(instance, validated_data)

class ChatRoomSerializer(serializers.ModelSerializer):
    other_user = serializers.SerializerMethodField()
    last_message = serializers.SerializerMethodField()

    class Meta:
        model = ChatRoom
        fields = ['id', 'type', 'name', 'other_user', 'last_message', 'created_at']

    def get_other_user(self, obj):
        request = self.context.get('request')
        if not request:
            return None
        
        # for private chat, return the other user
        if obj.type == 'private':
            # find first participant that is not request.user
            other_participant = obj.participants.exclude(user=request.user).first()
            if other_participant:
                return UserSerializer(other_participant.user).data
        return None

    def get_last_message(self, obj):
        last_msg = obj.messages.order_by('-created_at').first()
        if last_msg:
            return MessageSerializer(last_msg).data
        return None

class MessageSerializer(serializers.ModelSerializer):
    sender_name = serializers.CharField(source='sender.username', read_only=True)
    
    class Meta:
        model = Message
        fields = ['id', 'room', 'sender', 'sender_name', 'content', 'message_type', 'created_at', 'is_deleted']
