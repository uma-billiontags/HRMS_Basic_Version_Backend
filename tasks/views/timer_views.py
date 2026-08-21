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
from .utils import _is_admin, _current_employee, timezone_now


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def get_active_session(request):
    employee = _current_employee(request)
    if employee is None:
        return Response({"detail": "Only employees have timer sessions."}, status=status.HTTP_403_FORBIDDEN)

    session = (
        TimerSession.objects.filter(employee=employee, end_time__isnull=True)
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
    employee = _current_employee(request)
    if employee is None:
        return Response({"detail": "Only employees can start a timer."}, status=status.HTTP_403_FORBIDDEN)

    task = get_object_or_404(Task, pk=pk)
    if task.assigned_to_id != employee.id:
        return Response({"detail": "This task isn't assigned to you."}, status=status.HTTP_403_FORBIDDEN)

    if task.task_status != Task.Status.NOT_STARTED:
        return Response(
            {"detail": "This task has already been started. Use Resume instead."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    with transaction.atomic():
        if TimerSession.objects.select_for_update().filter(employee=employee, end_time__isnull=True).exists():
            return Response(
                {"detail": "You already have an active timer running on another task. Pause or submit it first."},
                status=status.HTTP_409_CONFLICT,
            )

        TimerSession.objects.create(task=task, employee=employee)
        task.task_status = Task.Status.IN_PROGRESS
        task.save(update_fields=["task_status"])

        log_activity(
            task, request.user, ActivityLog.Action.STARTED,
            from_status="not_started", to_status="in_progress",
        )

    return Response(TaskListSerializer(task).data)

from django.db import transaction

@api_view(["POST"])
@permission_classes([IsAuthenticated])
def pause_task(request, pk):
    """
    POST /api/tasks/<id>/pause/
    Closes the current open session, calculates its duration, recalculates
    the task's total time, and sets status = Paused.
    """
    employee = _current_employee(request)
    if employee is None:
        return Response({"detail": "Only employees can pause a timer."}, status=status.HTTP_403_FORBIDDEN)

    task = get_object_or_404(Task, pk=pk)
    if task.assigned_to_id != employee.id:
        return Response({"detail": "This task isn't assigned to you."}, status=status.HTTP_403_FORBIDDEN)

    session = TimerSession.objects.filter(task=task, employee=employee, end_time__isnull=True).first()
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
    employee = _current_employee(request)
    if employee is None:
        return Response({"detail": "Only employees can resume a timer."}, status=status.HTTP_403_FORBIDDEN)

    task = get_object_or_404(Task, pk=pk)
    if task.assigned_to_id != employee.id:
        return Response({"detail": "This task isn't assigned to you."}, status=status.HTTP_403_FORBIDDEN)

    if task.task_status not in (Task.Status.PAUSED, Task.Status.REWORK_NEEDED):
        return Response(
            {"detail": "This task isn't paused or awaiting rework, so it can't be resumed."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    with transaction.atomic():
        if TimerSession.objects.select_for_update().filter(employee=employee, end_time__isnull=True).exists():
            return Response(
                {"detail": "You already have an active timer running on another task. Pause or submit it first."},
                status=status.HTTP_409_CONFLICT,
            )

        from_status = task.task_status
        session = TimerSession.objects.create(
            task=task, employee=employee,
            is_rework_session=(task.task_status == Task.Status.REWORK_NEEDED),
        )
        task.task_status = Task.Status.IN_PROGRESS
        task.save(update_fields=["task_status"])

        log_activity(
            task, request.user, ActivityLog.Action.RESUMED,
            from_status=from_status, to_status="in_progress",
            details={"session_id": session.id, "is_rework_session": session.is_rework_session},
        )

    return Response(TaskListSerializer(task).data)

from ..models import Task, TimerSession, TaskAttachment

@api_view(["POST"])
@permission_classes([IsAuthenticated])
def submit_task(request, pk):
    employee = _current_employee(request)
    if employee is None:
        return Response({"detail": "Only employees can submit a task."}, status=status.HTTP_403_FORBIDDEN)

    task = get_object_or_404(Task, pk=pk)
    if task.assigned_to_id != employee.id:
        return Response({"detail": "This task isn't assigned to you."}, status=status.HTTP_403_FORBIDDEN)

    if task.task_status not in (Task.Status.IN_PROGRESS, Task.Status.PAUSED):
        return Response(
            {"detail": "This task must be in progress or paused to submit it."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    serializer = TaskSubmitSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)

    with transaction.atomic():
        open_session = TimerSession.objects.filter(task=task, employee=employee, end_time__isnull=True).first()
        if open_session:
            open_session.close()

        from_status = task.task_status
        task.task_sheet_link = serializer.validated_data["task_sheet_link"]
        task.employee_remarks = serializer.validated_data["employee_remarks"]
        task.submitted_date = timezone_now()
        task.task_status = Task.Status.RESUBMITTED if task.rework_count > 0 else Task.Status.SUBMITTED
        task.save(update_fields=["task_sheet_link", "employee_remarks", "submitted_date", "task_status"])

        # Images are entirely optional — loop is a no-op if nothing was sent
        for f in request.FILES.getlist("attachments"):
            TaskAttachment.objects.create(task=task, file=f, uploaded_by=employee)

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
    """
    GET /api/tasks/<id>/sessions/
    Session history for a task — admin can see any task's sessions,
    employee can only see sessions for their own assigned task.
    """
    task = get_object_or_404(Task, pk=pk)
    if not _is_admin(request) and task.assigned_to_id != request.user.instance.id:
        return Response({"detail": "You can't view sessions for this task."}, status=status.HTTP_403_FORBIDDEN)

    sessions = task.sessions.all()
    return Response(TimerSessionSerializer(sessions, many=True).data)
