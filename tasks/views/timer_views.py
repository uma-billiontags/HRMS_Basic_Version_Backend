# Timer Flow
# Employee start/pause/resume/submit cycle.

from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from accounts.models import Employee
from ..models import Task, TimerSession, TaskAttachment
from ..activity import ActivityLog, log_activity
from django.db import transaction
from ..activity import ActivityLog, log_activity
from ..serializers import (
    TaskListSerializer, TimerSessionSerializer, TaskSubmitSerializer,
)

from .utils import _is_admin, _current_task_actor, _is_task_assignee, timezone_now


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def get_active_session(request):
    kind, actor = _current_task_actor(request)
    if actor is None:
        return Response({"active": False, "task": None, "task_name": None, "session": None})

    owner_filter = {"employee": actor} if kind == "employee" else {"admin": actor}
    session = (
        TimerSession.objects.filter(end_time__isnull=True, **owner_filter)
        .select_related("task")
        .first()
    )
    if not session:
        return Response({"active": False, "task": None, "task_name": None, "session": None})

    return Response({
        "active": True,
        "task": session.task_id,
        "task_name": session.task.task_name,
        "session": TimerSessionSerializer(session).data,
    })


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def start_task(request, pk):
    kind, actor = _current_task_actor(request)
    if actor is None:
        return Response({"detail": "Only employees or admins can start a timer."}, status=status.HTTP_403_FORBIDDEN)

    task = get_object_or_404(Task, pk=pk)
    if not _is_task_assignee(request, task):
        return Response({"detail": "This task isn't assigned to you."}, status=status.HTTP_403_FORBIDDEN)

    if task.task_status != Task.Status.NOT_STARTED:
        return Response({"detail": "This task has already been started. Use Resume instead."}, status=status.HTTP_400_BAD_REQUEST)

    with transaction.atomic():
        owner_filter = {"employee": actor} if kind == "employee" else {"admin": actor}
        
        # ✅ FIX: Exclude THIS task so an orphaned session on the same task doesn't block it
        if TimerSession.objects.select_for_update().filter(end_time__isnull=True, **owner_filter).exclude(task=task).exists():
            return Response({"detail": "You already have an active timer running on another task. Pause or submit it first."}, status=status.HTTP_409_CONFLICT)

        # ✅ FIX: If there was a leftover open session on this task, close it cleanly
        task.sessions.filter(end_time__isnull=True, **owner_filter).update(end_time=timezone_now())

        session_kwargs = {"task": task, **owner_filter}
        TimerSession.objects.create(**session_kwargs)
        task.task_status = Task.Status.IN_PROGRESS
        task.save(update_fields=["task_status"])
        log_activity(task, request.user, ActivityLog.Action.STARTED, from_status="not_started", to_status="in_progress")

    return Response(TaskListSerializer(task).data)

@api_view(["POST"])
@permission_classes([IsAuthenticated])
def pause_task(request, pk):
    kind, actor = _current_task_actor(request)
    if actor is None:
        return Response({"detail": "Only employees or admins can pause a timer."}, status=status.HTTP_403_FORBIDDEN)

    task = get_object_or_404(Task, pk=pk)
    if not _is_task_assignee(request, task):
        return Response({"detail": "This task isn't assigned to you."}, status=status.HTTP_403_FORBIDDEN)

    owner_filter = {"employee": actor} if kind == "employee" else {"admin": actor}
    session = TimerSession.objects.filter(task=task, end_time__isnull=True, **owner_filter).first()
    if not session:
        return Response({"detail": "There's no active timer session to pause."}, status=status.HTTP_400_BAD_REQUEST)

    with transaction.atomic():
        session.close()
        task.task_status = Task.Status.PAUSED
        task.save(update_fields=["task_status"])
        task.recalc_total_time()
        log_activity(
            task, request.user, ActivityLog.Action.PAUSED,
            from_status="in_progress", to_status="paused",
            details={"session_id": session.id, "duration_seconds": session.duration_seconds},
        )

    return Response(TaskListSerializer(task).data)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def resume_task(request, pk):
    kind, actor = _current_task_actor(request)
    if actor is None:
        return Response({"detail": "Only employees or admins can resume a timer."}, status=status.HTTP_403_FORBIDDEN)

    task = get_object_or_404(Task, pk=pk)
    if not _is_task_assignee(request, task):
        return Response({"detail": "This task isn't assigned to you."}, status=status.HTTP_403_FORBIDDEN)

    if task.task_status not in (Task.Status.PAUSED, Task.Status.REWORK_NEEDED):
        return Response({"detail": "This task isn't paused or awaiting rework, so it can't be resumed."}, status=status.HTTP_400_BAD_REQUEST)

    with transaction.atomic():
        owner_filter = {"employee": actor} if kind == "employee" else {"admin": actor}
                # In resume_task:
        if TimerSession.objects.select_for_update().filter(end_time__isnull=True, **owner_filter).exclude(task=task).exists():
            return Response({"detail": "You already have an active timer running on another task. Pause or submit it first."}, status=status.HTTP_409_CONFLICT)
        
        from_status = task.task_status
        session = TimerSession.objects.create(
            task=task, is_rework_session=(task.task_status == Task.Status.REWORK_NEEDED), **owner_filter
        )
        task.task_status = Task.Status.IN_PROGRESS
        task.save(update_fields=["task_status"])
        log_activity(
            task, request.user, ActivityLog.Action.RESUMED,
            from_status=from_status, to_status="in_progress",
            details={"session_id": session.id, "is_rework_session": session.is_rework_session},
        )

    return Response(TaskListSerializer(task).data)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def submit_task(request, pk):
    kind, actor = _current_task_actor(request)
    if actor is None:
        return Response({"detail": "Only employees or admins can submit a task."}, status=status.HTTP_403_FORBIDDEN)

    task = get_object_or_404(Task, pk=pk)
    if not _is_task_assignee(request, task):
        return Response({"detail": "This task isn't assigned to you."}, status=status.HTTP_403_FORBIDDEN)

    if task.task_status not in (Task.Status.IN_PROGRESS, Task.Status.PAUSED):
        return Response({"detail": "This task must be in progress or paused to submit it."}, status=status.HTTP_400_BAD_REQUEST)

    serializer = TaskSubmitSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)

    owner_filter = {"employee": actor} if kind == "employee" else {"admin": actor}

    with transaction.atomic():
        open_session = TimerSession.objects.filter(task=task, end_time__isnull=True, **owner_filter).first()
        if open_session:
            open_session.close()

        from_status = task.task_status
        task.task_sheet_link = serializer.validated_data["task_sheet_link"]
        task.employee_remarks = serializer.validated_data["employee_remarks"]
        task.submitted_date = timezone_now()
        task.task_status = Task.Status.RESUBMITTED if task.rework_count > 0 else Task.Status.SUBMITTED
        task.save(update_fields=["task_sheet_link", "employee_remarks", "submitted_date", "task_status"])

        attachment_kwargs = {"uploaded_by": actor} if kind == "employee" else {"uploaded_by_admin": actor}
        for f in request.FILES.getlist("attachments"):
            TaskAttachment.objects.create(task=task, file=f, **attachment_kwargs)

        if open_session:
            task.recalc_total_time()

        log_activity(
            task, request.user, ActivityLog.Action.SUBMITTED,
            from_status=from_status, to_status=task.task_status,
            details={"task_sheet_link": task.task_sheet_link, "attachment_count": len(request.FILES.getlist("attachments"))},
        )

    return Response(TaskListSerializer(task).data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def get_task_sessions(request, pk):
    task = get_object_or_404(Task, pk=pk)
    if not _is_admin(request) and not _is_task_assignee(request, task):
        return Response({"detail": "You can't view sessions for this task."}, status=status.HTTP_403_FORBIDDEN)
    sessions = task.sessions.all()
    return Response(TimerSessionSerializer(sessions, many=True).data)