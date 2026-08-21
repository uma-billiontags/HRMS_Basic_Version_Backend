# Hold / Cancel 
# Task state transitions outside the normal timer/review flow.
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from tasks.views.utils import _is_admin
from rest_framework import status
from .utils import _is_admin, _owns_task_for_management
from rest_framework import status
from django.shortcuts import get_object_or_404
from django.db import transaction
from ..activity import ActivityLog, log_activity
from ..models import Task
from ..serializers import ( TaskListSerializer )



@api_view(["POST"])
@permission_classes([IsAuthenticated])
def hold_task(request, pk):
    task = get_object_or_404(Task, pk=pk)
    if not _owns_task_for_management(request, task):
        return Response({"detail": "You can only hold tasks you created."}, status=status.HTTP_403_FORBIDDEN)
    if task.task_status not in NON_FINAL_STATUSES:
        return Response({"detail": "This task can't be put on hold from its current status."}, status=status.HTTP_400_BAD_REQUEST)

    with transaction.atomic():
        _close_open_session_if_any(task)
        old_status = task.task_status
        task.status_before_hold = old_status
        task.task_status = Task.Status.ON_HOLD
        task.save(update_fields=["task_status", "status_before_hold"])
        log_activity(task, request.user, ActivityLog.Action.HOLD, from_status=old_status, to_status="on_hold")

    return Response(TaskListSerializer(task).data)

@api_view(["POST"])
@permission_classes([IsAuthenticated])
def release_hold(request, pk):
    if not _is_admin(request):
        return Response({"detail": "Admin only."}, status=status.HTTP_403_FORBIDDEN)
    task = get_object_or_404(Task, pk=pk)
    if task.task_status != Task.Status.ON_HOLD:
        return Response({"detail": "This task isn't on hold."}, status=status.HTTP_400_BAD_REQUEST)

    with transaction.atomic():
        restored = task.status_before_hold or Task.Status.NOT_STARTED
        task.task_status = restored
        task.status_before_hold = ""
        task.save(update_fields=["task_status", "status_before_hold"])
        log_activity(task, request.user, ActivityLog.Action.RELEASED_HOLD, from_status="on_hold", to_status=restored)

    return Response(TaskListSerializer(task).data)

@api_view(["POST"])
@permission_classes([IsAuthenticated])
def cancel_task(request, pk):
    task = get_object_or_404(Task, pk=pk)
    if not _owns_task_for_management(request, task):
        return Response({"detail": "You can only hold tasks you created."}, status=status.HTTP_403_FORBIDDEN)
    if task.task_status not in NON_FINAL_STATUSES + [Task.Status.ON_HOLD]:
        return Response({"detail": "This task can't be cancelled from its current status."}, status=status.HTTP_400_BAD_REQUEST)

    reason = request.data.get("reason", "")

    with transaction.atomic():
        _close_open_session_if_any(task)
        old_status = task.task_status
        task.task_status = Task.Status.CANCELLED
        task.cancel_reason = reason                       # ← NEW
        task.save(update_fields=["task_status", "cancel_reason"])   # ← include it in update_fields
        log_activity(task, request.user, ActivityLog.Action.CANCELLED, from_status=old_status, to_status="cancelled",
                     details={"reason": reason})

    return Response(TaskListSerializer(task).data)

def _close_open_session_if_any(task):
    """Shared by hold/cancel — closes any running session so no time is lost."""
    open_session = task.sessions.filter(end_time__isnull=True).first()
    if open_session:
        open_session.close()
        task.recalc_total_time()

NON_FINAL_STATUSES = [s for s in Task.Status.values if s not in
                      (Task.Status.COMPLETED, Task.Status.CANCELLED)]



