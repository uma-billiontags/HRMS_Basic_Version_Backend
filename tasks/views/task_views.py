# Task CRUD & Listing
# Core task creation, assignment, and listing across roles.

from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from accounts.models import Employee
from ..models import Task
from ..serializers import (
    TaskListSerializer, TaskCreateSerializer, TaskAssignSerializer,
    TaskCreateAssignSerializer, 
)
from ..activity import ActivityLog, log_activity
from django.db import transaction
from ..activity import ActivityLog, log_activity
from ..serializers import (
    TaskListSerializer, TaskCreateSerializer, TaskAssignSerializer, TaskCreateAssignSerializer
)
from .utils import _is_admin, _current_employee, _is_tl, _can_manage_tasks, _owns_task_for_management



@api_view(["GET"])
@permission_classes([IsAuthenticated])
def get_all_tasks(request):
    tasks = Task.objects.all()

    return Response(TaskListSerializer(tasks, many=True).data)

@api_view(["POST"])
@permission_classes([IsAuthenticated])
def create_task(request):
    if not _can_manage_tasks(request):
        return Response({"detail": "Only admins or team leads can create tasks."}, status=status.HTTP_403_FORBIDDEN)

    serializer = TaskCreateSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)

    if _is_admin(request):
        task = serializer.save(assigned_by_admin=request.user.instance)
    else:
        task = serializer.save(assigned_by_employee=request.user.instance)

    return Response(TaskListSerializer(task).data, status=status.HTTP_201_CREATED)


@api_view(["PATCH"])
@permission_classes([IsAuthenticated])
def assign_task(request, pk):
    task = get_object_or_404(Task, pk=pk)
    if not _owns_task_for_management(request, task):
        return Response(
            {"detail": "You can only assign or reassign tasks you created."},
            status=status.HTTP_403_FORBIDDEN,
        )
    serializer = TaskAssignSerializer(task, data=request.data, partial=True)
    serializer.is_valid(raise_exception=True)
    task = serializer.save(task_status=Task.Status.NOT_STARTED)
    return Response(TaskListSerializer(task).data)

@api_view(["POST"])
@permission_classes([IsAuthenticated])
def create_and_assign_task(request):
    """
    POST /api/tasks/create_and_assign/
    Atomic create+assign — either both succeed or neither does. No orphaned
    unassigned task left behind if validation fails partway through.
    """
    if not _can_manage_tasks(request):
        return Response({"detail": "Only admins or team leads can create tasks."}, status=status.HTTP_403_FORBIDDEN)

    serializer = TaskCreateAssignSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)

    with transaction.atomic():
        if _is_admin(request):
            task = serializer.save(
                assigned_by_admin=request.user.instance,
                task_status=Task.Status.NOT_STARTED,
            )
        else:
            task = serializer.save(
                assigned_by_employee=request.user.instance,
                task_status=Task.Status.NOT_STARTED,
            )

        log_activity(
            task, request.user, ActivityLog.Action.CREATED,
            to_status="not_started",
            details={
                "assigned_to": task.assigned_to.name if task.assigned_to_id else None,
                "priority": task.priority,
                "due_date": str(task.due_date) if task.due_date else None,
                "allotted_time": str(task.allotted_time) if task.allotted_time is not None else None,
            },
        )

    return Response(TaskListSerializer(task).data, status=status.HTTP_201_CREATED)

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def get_tl_tasks(request):
    """
    GET /api/tasks/tl_tasks/
    TL-only. Every task this TL personally created — self-assigned or
    handed to someone else. Mirrors get_all_tasks but scoped instead of
    system-wide; admins keep using get_all_tasks for the full picture.
    """
    if not _is_tl(request):
        return Response({"detail": "Team leads only."}, status=status.HTTP_403_FORBIDDEN)

    employee = _current_employee(request)
    tasks = Task.objects.filter(assigned_by_employee=employee)
  
    return Response(TaskListSerializer(tasks, many=True).data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def get_my_tasks(request):
    """
    GET /api/tasks/my_tasks/
    Employee-only. Every task assigned to the logged-in employee —
    used by the "My Tasks Only" scope on Active_Task_Employee, and by
    forceScope="mine" (the TL's "My Task" screen).
    """
    employee = _current_employee(request)
    if employee is None:
        return Response({"detail": "Employees only."}, status=status.HTTP_403_FORBIDDEN)

    tasks = Task.objects.filter(assigned_to=employee)
    
    return Response(TaskListSerializer(tasks, many=True).data)

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def get_all_departments(request):
    """GET /api/tasks/get_all_departments/"""
    departments = (
        Employee.objects.exclude(department="")
        .values_list("department", flat=True)
        .distinct()
        .order_by("department")
    )
    return Response([{"id": name, "name": name} for name in departments])


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def get_all_employees(request):
    """GET /api/tasks/get_all_employees/?department=Engineering"""
    employees = Employee.objects.all()
    department = request.query_params.get("department")
    if department:
        employees = employees.filter(department=department)
    data = [{"id": e.id, "name": e.name, "department": e.department} for e in employees]
    return Response(data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def get_priority_choices(request):
    """GET /api/tasks/get_priority_choices/"""
    return Response([{"value": value, "label": label} for value, label in Task.Priority.choices])

