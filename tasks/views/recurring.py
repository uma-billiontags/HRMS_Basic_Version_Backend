# tasks/recurring.py
from datetime import timedelta
from django.db.models import Max, Q
from django.utils import timezone
from tasks.models import Task, RecurringTaskDefinition
from ..serializers import TaskListSerializer, RecurringTaskDefinitionSerializer, RecurringTaskDefinitionCreateSerializer
from .utils import _is_admin, _current_employee, _is_tl, _can_manage_tasks

from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from django.shortcuts import get_object_or_404


def generate_recurring_tasks(as_of_date=None):
    """
    Call this before any task-listing query. Walks every active
    RecurringTaskDefinition from wherever it last left off, up through
    as_of_date (default: today) — so even if nobody opened the app for a
    few days, the missed days get backfilled in one pass, never silently
    dropped.

    Idempotent: safe to call many times per day (get_or_create on the
    (definition, date) pair means repeat calls are no-ops).
    """
    as_of_date = as_of_date or timezone.localdate()

    defs = RecurringTaskDefinition.objects.filter(
        is_active=True,
        start_date__lte=as_of_date,
    ).filter(Q(end_date__isnull=True) | Q(end_date__gte=as_of_date))

    for d in defs:
        last_generated = d.generated_tasks.aggregate(m=Max("generated_for_date"))["m"]
        cursor = (last_generated + timedelta(days=1)) if last_generated else d.start_date

        # Never walk past today or past this definition's own end_date.
        walk_until = min(as_of_date, d.end_date) if d.end_date else as_of_date

        while cursor <= walk_until:
            if cursor.weekday() in d.weekdays:   # ← changed: Mon=0 ... Sun=6
                Task.objects.get_or_create(
                    recurring_source=d,
                    generated_for_date=cursor,
                    defaults=dict(
                        task_name=d.task_name,
                        task_details=d.task_details,
                        assigned_to=d.assigned_to,
                        priority=d.priority,
                        allotted_time=d.allotted_time,
                        due_date=cursor,
                        assigned_by_admin=d.assigned_by_admin,
                        assigned_by_employee=d.assigned_by_employee,
                        task_status=Task.Status.NOT_STARTED,
                    ),
                )
            cursor += timedelta(days=1)
            
# ── Recurring task definition views ────────────────────────────────────────
# Create/list/stop recurring task definitions.

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def get_recurring_tasks(request):
    """GET /api/tasks/recurring/ — admin-only, sitewide management list."""
    if not _is_admin(request):
        return Response({"detail": "Admin only."}, status=status.HTTP_403_FORBIDDEN)
    defs = RecurringTaskDefinition.objects.all()
    return Response(RecurringTaskDefinitionSerializer(defs, many=True).data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def get_my_recurring_tasks(request):
    """
    GET /api/tasks/recurring/mine/
    TL-only. Mirrors tl_tasks vs get_all_tasks — only recurring rules
    THIS TL personally created, not admin-created or other TLs' rules.
    """
    if not _is_tl(request):
        return Response({"detail": "Team leads only."}, status=status.HTTP_403_FORBIDDEN)
    employee = _current_employee(request)
    defs = RecurringTaskDefinition.objects.filter(assigned_by_employee=employee)
    return Response(RecurringTaskDefinitionSerializer(defs, many=True).data)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def create_recurring_task(request):
    """
    POST /api/tasks/recurring/create/
    Creates the recurring rule AND immediately generates today's (and any
    already-due) occurrence, so the employee sees it without waiting for
    a scheduled job.
    """
    if not _can_manage_tasks(request):
        return Response({"detail": "Only admins or team leads can create tasks."}, status=status.HTTP_403_FORBIDDEN)

    serializer = RecurringTaskDefinitionCreateSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)

    if _is_admin(request):
        definition = serializer.save(assigned_by_admin=request.user.instance)
    else:
        definition = serializer.save(assigned_by_employee=request.user.instance)

    # Generate immediately — don't make the employee wait for the next
    # dashboard load elsewhere to see day 1.
    generate_recurring_tasks()

    return Response(RecurringTaskDefinitionSerializer(definition).data, status=status.HTTP_201_CREATED)


def _owns_recurring_for_management(request, definition):
    """Admin: any rule. TL: only rules they personally created — same
    ownership pattern as _owns_task_for_management for regular tasks."""
    if _is_admin(request):
        return True
    employee = _current_employee(request)
    return employee is not None and employee.role == "TL" and definition.assigned_by_employee_id == employee.id


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def stop_recurring_task(request, pk):
    """
    POST /api/tasks/recurring/<id>/stop/
    Admin can stop any rule; a TL can only stop rules they created.
    Deactivates the rule — no more future days generated. Already-generated
    Task rows (including today's) are untouched, same as hold/cancel: history
    is never rewritten.
    """
    definition = get_object_or_404(RecurringTaskDefinition, pk=pk)
    if not _owns_recurring_for_management(request, definition):
        return Response({"detail": "You can only stop recurring tasks you created."}, status=status.HTTP_403_FORBIDDEN)

    definition.is_active = False
    definition.save(update_fields=["is_active"])
    return Response(RecurringTaskDefinitionSerializer(definition).data)


# ── Lazy-fallback hooks for the listing views ───────────────────────────────
# tasks/views/task_crud.py — same file as get_all_tasks / get_my_tasks / get_tl_tasks

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def get_all_tasks(request):
    generate_recurring_tasks()          # ← ADD THIS LINE
    tasks = Task.objects.all()
    return Response(TaskListSerializer(tasks, many=True).data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def get_my_tasks(request):
    generate_recurring_tasks()          # ← ADD THIS LINE
    employee = _current_employee(request)
    if employee is None:
        return Response({"detail": "Employees only."}, status=status.HTTP_403_FORBIDDEN)
    tasks = Task.objects.filter(assigned_to=employee)
    return Response(TaskListSerializer(tasks, many=True).data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def get_tl_tasks(request):
    generate_recurring_tasks()          # ← ADD THIS LINE
    if not _is_tl(request):
        return Response({"detail": "Team leads only."}, status=status.HTTP_403_FORBIDDEN)
    employee = _current_employee(request)
    tasks = Task.objects.filter(assigned_by_employee=employee)
    return Response(TaskListSerializer(tasks, many=True).data)