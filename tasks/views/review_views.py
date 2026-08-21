# Review Flow
# Admin/TL review queue and decisions.
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from .utils import _is_admin, _current_employee, _can_review_task, _is_tl
from rest_framework import status
from django.shortcuts import get_object_or_404
from django.db import transaction
from ..activity import ActivityLog, log_activity
from django.utils import timezone
from ..models import Task
from ..serializers import ( TaskListSerializer, ReviewApproveSerializer, ReviewReworkSerializer )


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def get_review_tasks(request):
    """
    GET /api/tasks/review_tasks/
    Admin-only. Shows every task waiting on a review decision —
    Submitted, Resubmitted, or already opened as Under Review.
    """
    if not _is_admin(request):
        return Response({"detail": "Only admins can view the review queue."}, status=status.HTTP_403_FORBIDDEN)

    tasks = Task.objects.filter(
        task_status__in=[Task.Status.SUBMITTED, Task.Status.RESUBMITTED, Task.Status.UNDER_REVIEW]
    )
    return Response(TaskListSerializer(tasks, many=True).data)

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def get_tl_review_tasks(request):
    """
    GET /api/tasks/tl_review_tasks/
    TL-only. Tasks THIS TL created and assigned to someone else — mirrors
    get_tl_tasks vs get_all_tasks. Self-assigned tasks are excluded; those
    go to the admin review queue instead, since a TL can't review their
    own submitted work.
    """
    if not _is_tl(request):
        return Response({"detail": "Team leads only."}, status=status.HTTP_403_FORBIDDEN)

    employee = _current_employee(request)
    tasks = Task.objects.filter(
        assigned_by_employee=employee,
        task_status__in=[Task.Status.SUBMITTED, Task.Status.RESUBMITTED, Task.Status.UNDER_REVIEW],
    ).exclude(assigned_to=employee)
    return Response(TaskListSerializer(tasks, many=True).data)

@api_view(["POST"])
@permission_classes([IsAuthenticated])
def start_review(request, pk):
    """
    POST /api/tasks/<id>/review/start/
    Admin or the TL who created this task. Submitted/Resubmitted -> Under Review.
    """
    task = get_object_or_404(Task, pk=pk)
    if not _can_review_task(request, task):
        return Response({"detail": "You can't review this task."}, status=status.HTTP_403_FORBIDDEN)

    if task.task_status not in (Task.Status.SUBMITTED, Task.Status.RESUBMITTED):
        return Response(
            {"detail": "Only submitted or resubmitted tasks can enter review."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    task.task_status = Task.Status.UNDER_REVIEW
    task.save(update_fields=["task_status"])
    return Response(TaskListSerializer(task).data)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def approve_task(request, pk):
    task = get_object_or_404(Task, pk=pk)
    if not _can_review_task(request, task):
        return Response({"detail": "You can't review this task."}, status=status.HTTP_403_FORBIDDEN)

    if task.task_status != Task.Status.UNDER_REVIEW:
        return Response(
            {"detail": "This task must be Under Review before it can be approved."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    serializer = ReviewApproveSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)

    with transaction.atomic():
        task.quality_of_task = serializer.validated_data["quality_of_task"]
        task.rating = serializer.validated_data["rating"]
        task.admin_remarks = serializer.validated_data["admin_remarks"]
        task.reviewed_date = timezone.now()
        task.task_status = Task.Status.COMPLETED
        
        # NEW — record who approved it
        if _is_admin(request):
            task.reviewed_by_admin = request.user.instance
            task.reviewed_by_employee = None
        else:
            task.reviewed_by_employee = request.user.instance
            task.reviewed_by_admin = None
            
        task.save(update_fields=[
            "quality_of_task", "rating", "admin_remarks", "reviewed_date", "task_status",
            "reviewed_by_admin", "reviewed_by_employee",   # ← add these
        ])

        log_activity(
            task, request.user, ActivityLog.Action.APPROVED,
            from_status="under_review", to_status="completed",
            details={"quality": task.quality_of_task, "rating": task.rating, "remarks": task.admin_remarks},
        )

    return Response(TaskListSerializer(task).data)

@api_view(["POST"])
@permission_classes([IsAuthenticated])
def request_rework(request, pk):
    task = get_object_or_404(Task, pk=pk)
    if not _can_review_task(request, task):
        return Response({"detail": "You can't review this task."}, status=status.HTTP_403_FORBIDDEN)

    if task.task_status != Task.Status.UNDER_REVIEW:
        return Response(
            {"detail": "This task must be Under Review before requesting rework."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    serializer = ReviewReworkSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)

    with transaction.atomic():
        task.admin_remarks = serializer.validated_data["admin_remarks"]
        task.reviewed_date = timezone.now()
        task.rework_count = task.rework_count + 1
        task.task_status = Task.Status.REWORK_NEEDED
        task.save(update_fields=["admin_remarks", "reviewed_date", "rework_count", "task_status"])

        log_activity(
            task, request.user, ActivityLog.Action.REWORK_REQUESTED,
            from_status="under_review", to_status="rework_needed",
            details={"admin_remarks": task.admin_remarks, "rework_count": task.rework_count},
        )

    return Response(TaskListSerializer(task).data)

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def get_rework_tasks(request):
    """
    GET /api/tasks/rework_tasks/
    Admin-only. Every task that has EVER been sent back for rework
    (rework_count > 0), regardless of current status — unlike the
    Review Queue (get_review_tasks), which only shows tasks currently
    sitting in submitted/resubmitted/under_review, this stays populated
    as the task moves through rework_needed -> in_progress -> paused ->
    resubmitted -> under_review -> completed, so admin never loses
    visibility on a task once it's been reworked.
    """
    if not _is_admin(request):
        return Response({"detail": "Admin only."}, status=status.HTTP_403_FORBIDDEN)

    tasks = Task.objects.filter(rework_count__gt=0)

    return Response(TaskListSerializer(tasks.order_by("-reviewed_date"), many=True).data)
