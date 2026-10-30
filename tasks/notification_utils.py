# tasks/notification_utils.py

from tasks.models import Notification
from accounts.models import Employee, Admin


def send_notification(recipient, title, message, event_type, action_url="", sender_name=""):
    """
    Creates a Notification record for either an Employee or an Admin.
    recipient can be an Employee instance, Admin instance, or a SimplePrincipal wrapper.
    """
    if not recipient:
        return None

    kwargs = {
        "title": title,
        "message": message,
        "event_type": event_type,
        "action_url": action_url,
        "sender_name": sender_name,
    }

    if isinstance(recipient, Employee):
        kwargs["recipient_employee"] = recipient
    elif isinstance(recipient, Admin):
        kwargs["recipient_admin"] = recipient
    elif hasattr(recipient, "role"):
        if recipient.role == "admin":
            kwargs["recipient_admin"] = recipient.instance
        else:
            kwargs["recipient_employee"] = recipient.instance
    else:
        return None

    try:
        return Notification.objects.create(**kwargs)
    except Exception as e:
        # Failsafe logging so a notification creation never breaks the primary business logic
        print(f"Failed to create notification: {e}")
        return None
