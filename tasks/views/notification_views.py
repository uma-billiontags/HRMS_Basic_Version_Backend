# tasks/views/notification_views.py

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from tasks.models import Notification
from tasks.serializers import NotificationSerializer
from tasks.views.utils import _is_admin, _current_employee, _current_admin


def _get_notification_queryset(request):
    """
    Returns the Notification queryset strictly scoped to the logged-in principal
    (Admin or Employee).
    """
    if _is_admin(request):
        admin_instance = _current_admin(request)
        if not admin_instance:
            return Notification.objects.none()
        return Notification.objects.filter(recipient_admin=admin_instance)
    
    emp_instance = _current_employee(request)
    if not emp_instance:
        return Notification.objects.none()
    return Notification.objects.filter(recipient_employee=emp_instance)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def get_notifications(request):
    """
    GET /api/tasks/notifications/?unread_only=true
    Returns the latest 40 notifications for the logged-in user.
    """
    qs = _get_notification_queryset(request)
    unread_only = request.query_params.get("unread_only", "").lower() in ("true", "1")
    if unread_only:
        qs = qs.filter(is_read=False)

    notifications = qs.order_by("-created_at")[:40]
    serializer = NotificationSerializer(notifications, many=True)
    return Response(serializer.data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def get_unread_notification_count(request):
    """
    GET /api/tasks/notifications/unread_count/
    Lightweight endpoint polled by the frontend header bell.
    """
    qs = _get_notification_queryset(request)
    unread_count = qs.filter(is_read=False).count()
    return Response({"unread_count": unread_count})


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def mark_notification_read(request, pk):
    """
    POST /api/tasks/notifications/<pk>/mark_read/
    Marks a single notification as read if it belongs to the logged-in user.
    """
    qs = _get_notification_queryset(request)
    notif = qs.filter(pk=pk).first()
    if not notif:
        return Response({"detail": "Notification not found."}, status=status.HTTP_404_NOT_FOUND)

    notif.is_read = True
    notif.save(update_fields=["is_read"])
    return Response({"detail": "Marked as read.", "id": pk})


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def mark_all_notifications_read(request):
    """
    POST /api/tasks/notifications/mark_all_read/
    Marks all notifications for the logged-in user as read.
    """
    qs = _get_notification_queryset(request)
    updated_count = qs.filter(is_read=False).update(is_read=True)
    return Response({"detail": "All notifications marked as read.", "updated_count": updated_count})
