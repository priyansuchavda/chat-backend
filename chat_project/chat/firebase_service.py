import logging
from pathlib import Path

import firebase_admin
from django.conf import settings
from firebase_admin import credentials, messaging

logger = logging.getLogger(__name__)

_firebase_initialized = False


def _normalize_data(data):
    """FCM requires all data payload values to be strings."""
    if not data:
        return {}
    return {str(key): str(value) for key, value in data.items()}


def initialize_firebase():
    """Initialize Firebase Admin SDK with credentials from settings."""
    global _firebase_initialized

    cred_path = Path(getattr(settings, 'FIREBASE_CREDENTIALS_PATH', ''))
    if not cred_path.exists():
        raise FileNotFoundError(f"Firebase credentials file not found: {cred_path}")

    if firebase_admin._apps:
        firebase_admin.delete_app(firebase_admin.get_app())

    cred = credentials.Certificate(str(cred_path))
    firebase_admin.initialize_app(cred)
    _firebase_initialized = True
    logger.info("Firebase Admin SDK initialized for project: %s", cred.project_id)
    print(f"✓ Firebase Admin SDK initialized (project: {cred.project_id})")


try:
    initialize_firebase()
except Exception as e:
    logger.error("Firebase initialization error: %s", e)
    print(f"❌ Firebase initialization error: {e}")


def send_notification(token, title, body, data=None):
    """Send a notification to a single device."""
    try:
        message = messaging.Message(
            notification=messaging.Notification(title=title, body=body),
            data=_normalize_data(data),
            token=token,
        )
        response = messaging.send(message)
        logger.info("Notification sent successfully: %s", response)
        return True
    except Exception as e:
        logger.error("Error sending notification: %s", e)
        return False


def _log_send_failures(tokens, response):
    """Log per-token Firebase errors for easier debugging."""
    for idx, send_response in enumerate(response.responses):
        if send_response.success:
            continue
        token_preview = f"{tokens[idx][:12]}...{tokens[idx][-8:]}" if tokens[idx] else "empty"
        error = send_response.exception
        print(f"❌ Failed token [{idx}] ({token_preview}): {error}")
        logger.error("Failed token [%s] (%s): %s", idx, token_preview, error)


def send_multicast_notification(tokens, title, body, data=None):
    """Send a notification to multiple devices."""
    if not tokens:
        print("⚠️ No tokens provided for notification")
        logger.warning("No tokens provided for notification")
        return {"success": 0, "failure": 0}

    if not _firebase_initialized:
        print("❌ Firebase is not initialized")
        logger.error("Firebase is not initialized")
        return {"success": 0, "failure": len(tokens)}

    try:
        print(f"📤 Sending notification to {len(tokens)} device(s)...")
        print(f"   Title: {title}")
        print(f"   Body: {body}")

        payload = _normalize_data(data)
        messages = [
            messaging.Message(
                notification=messaging.Notification(title=title, body=body),
                data=payload,
                token=token,
            )
            for token in tokens
        ]

        response = messaging.send_each(messages)

        print(
            f"✅ Notifications sent - Success: {response.success_count}, "
            f"Failure: {response.failure_count}"
        )
        logger.info(
            "Notifications sent - Success: %s, Failure: %s",
            response.success_count,
            response.failure_count,
        )

        if response.failure_count:
            _log_send_failures(tokens, response)

        return {
            "success": response.success_count,
            "failure": response.failure_count,
        }
    except Exception as e:
        error_msg = f"Error sending notifications: {e}"
        print(f"❌ {error_msg}")
        logger.error(error_msg)
        return {"success": 0, "failure": len(tokens)}


def send_message_notification(sender, recipient, message_content, room_id=None, message_id=None):
    """Send a push notification to a user (typically when their app is closed/backgrounded)."""
    from chat.models import DeviceToken

    # Skip if user is actively online (presence socket connected = app open)
    if hasattr(recipient, 'profile') and recipient.profile.is_online:
        logger.info("Skipping push for %s — app is online", recipient.username)
        return

    device_tokens = list(
        DeviceToken.objects.filter(user=recipient, is_active=True).values_list('token', flat=True)
    )

    if not device_tokens:
        logger.info("No active device tokens for user %s", recipient.username)
        return

    title = f"New message from {sender.first_name or sender.username}"
    body = message_content[:100] if len(message_content) > 100 else message_content
    data = {
        "sender_id": str(sender.id),
        "sender_name": sender.username,
        "type": "message",
        "room_id": str(room_id) if room_id else "",
        "message_id": str(message_id) if message_id else "",
    }

    send_multicast_notification(device_tokens, title, body, data)


def notify_room_participants(sender, room, message_content, message_id=None):
    """Notify all other participants in a room about a new message."""
    from chat.models import ChatParticipant

    recipients = (
        ChatParticipant.objects.filter(room=room)
        .exclude(user=sender)
        .select_related('user', 'user__profile')
    )

    for participant in recipients:
        try:
            send_message_notification(
                sender,
                participant.user,
                message_content,
                room_id=room.id,
                message_id=message_id,
            )
        except Exception as e:
            logger.error("Failed to notify %s: %s", participant.user.username, e)
