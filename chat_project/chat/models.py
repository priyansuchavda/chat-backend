from django.db import models
from django.contrib.auth.models import User

class ChatRoom(models.Model):
    ROOM_TYPES = (
        ('private', 'Private'),
        ('group', 'Group'),
    )
    type = models.CharField(max_length=10, choices=ROOM_TYPES, default='private')
    name = models.CharField(max_length=255, blank=True, null=True) # for group names
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.type} Room - {self.id}"

class Message(models.Model):
    MESSAGE_TYPES = (
        ('text', 'Text'),
        ('image', 'Image'),
        ('file', 'File'),
    )
    room = models.ForeignKey(ChatRoom, on_delete=models.CASCADE, related_name='messages')
    sender = models.ForeignKey(User, on_delete=models.CASCADE)
    content = models.TextField()
    message_type = models.CharField(max_length=10, choices=MESSAGE_TYPES, default='text')
    created_at = models.DateTimeField(auto_now_add=True)
    is_deleted = models.BooleanField(default=False)

    def __str__(self):
        return f"Message by {self.sender} in Room {self.room_id}"

class ChatParticipant(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    room = models.ForeignKey(ChatRoom, on_delete=models.CASCADE, related_name='participants')
    last_read_message = models.ForeignKey(Message, on_delete=models.SET_NULL, null=True, blank=True, related_name='last_reads')

    class Meta:
        unique_together = ('user', 'room')

class MessageStatus(models.Model):
    STATUS_CHOICES = (
        ('sent', 'Sent'),
        ('delivered', 'Delivered'),
        ('read', 'Read'),
    )
    message = models.ForeignKey(Message, on_delete=models.CASCADE, related_name='statuses')
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='sent')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('message', 'user')

class Profile(models.Model):
    GENDER_CHOICES = (
        ('male', 'Male'),
        ('female', 'Female'),
        ('other', 'Other'),
    )
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    avatar = models.ImageField(upload_to='avatars/', null=True, blank=True)
    gender = models.CharField(max_length=10, choices=GENDER_CHOICES, null=True, blank=True)
    dob = models.DateField(null=True, blank=True)
    is_online = models.BooleanField(default=False)
    last_seen = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user.username}'s Profile"

# Signals to handle Profile creation
from django.db.models.signals import post_save
from django.dispatch import receiver

@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
    if created:
        Profile.objects.create(user=instance)

@receiver(post_save, sender=User)
def save_user_profile(sender, instance, **kwargs):
    instance.profile.save()
