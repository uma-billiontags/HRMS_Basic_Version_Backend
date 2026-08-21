# Task Master (catalog)

from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from decimal import Decimal
from tasks.views.utils import _is_admin, _can_manage_tasks   
from ..models import TaskMaster
from ..serializers import ( TaskMasterSerializer, TaskMasterWriteSerializer
)
from decimal import Decimal


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def get_task_master_list(request):
    """GET /api/tasks/task_master/ — dropdown source for Create Task's Task Name field."""
    templates = TaskMaster.objects.filter(is_active=True).order_by("task_name")
    return Response(TaskMasterSerializer(templates, many=True).data)

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def get_all_task_master(request):
    """
    GET /api/tasks/task_master/get_all/
    Admin-only management list — unlike get_task_master_list (the dropdown
    source, active-only), this one shows everything including inactive rows
    so admins can see/edit/reactivate them.
    """
    if not _is_admin(request):
        return Response({"detail": "Admin only."}, status=status.HTTP_403_FORBIDDEN)
    templates = TaskMaster.objects.all().order_by("task_name")
    return Response(TaskMasterSerializer(templates, many=True).data)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def create_task_master(request):
    """POST /api/tasks/task_master/create/"""
    if not _can_manage_tasks(request):          # ← changed from _is_admin
        return Response({"detail": "Admins or team leads only."}, status=status.HTTP_403_FORBIDDEN)

    serializer = TaskMasterWriteSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    template = serializer.save()
    return Response(TaskMasterSerializer(template).data, status=status.HTTP_201_CREATED)


@api_view(["PATCH"])
@permission_classes([IsAuthenticated])
def edit_task_master(request, pk):
    """PATCH /api/tasks/task_master/<id>/edit/"""
    if not _is_admin(request):
        return Response({"detail": "Admin only."}, status=status.HTTP_403_FORBIDDEN)

    template = get_object_or_404(TaskMaster, pk=pk)
    serializer = TaskMasterWriteSerializer(template, data=request.data, partial=True)
    serializer.is_valid(raise_exception=True)
    template = serializer.save()
    return Response(TaskMasterSerializer(template).data)


@api_view(["DELETE"])
@permission_classes([IsAuthenticated])
def delete_task_master(request, pk):
    """
    DELETE /api/tasks/task_master/<id>/delete/
    Hard delete. Safe to do even though old Task rows may have been created
    from this template, since Task.task_name is a plain copied string with
    no FK back to TaskMaster — deleting a template never touches past tasks.
    """
    if not _is_admin(request):
        return Response({"detail": "Admin only."}, status=status.HTTP_403_FORBIDDEN)

    template = get_object_or_404(TaskMaster, pk=pk)
    name = template.task_name
    template.delete()
    return Response({"detail": f'"{name}" deleted.'}, status=status.HTTP_200_OK)

@api_view(["POST"])
@permission_classes([IsAuthenticated])
def bulk_create_task_master(request):
    """
    POST /api/tasks/task_master/bulk_create/
    body: { "items": [{"project_name": "...", "task_name": "...", "default_hours": 3}, ...] }
    Processes every row independently — one bad row doesn't block the rest.
    Duplicate (project_name, task_name, default_hours) case-insensitive is skipped, not an error.
    """
    if not _is_admin(request):
        return Response({"detail": "Admin only."}, status=status.HTTP_403_FORBIDDEN)

    items = request.data.get("items")
    if not isinstance(items, list) or not items:
        return Response({"detail": "items must be a non-empty list."}, status=status.HTTP_400_BAD_REQUEST)

    created, skipped, errors = [], [], []

    for idx, row in enumerate(items):
        row_num = idx + 1
        project = str(row.get("project_name", "")).strip()
        name = str(row.get("task_name", "")).strip()
        raw_hours = row.get("default_hours")

        if not project:
            errors.append({"row": row_num, "task_name": name, "error": "Missing project name"})
            continue
        if not name:
            errors.append({"row": row_num, "error": "Missing task name"})
            continue

        try:
            hours = Decimal(str(raw_hours))
            if hours <= 0:
                raise ValueError
        except Exception:
            errors.append({"row": row_num, "task_name": name, "error": f"Invalid hours: {raw_hours!r}"})
            continue

        if TaskMaster.objects.filter(
            task_name__iexact=name, default_hours=hours, project_name__iexact=project
        ).exists():
            skipped.append({
                "row": row_num, "task_name": name,
                "reason": f'Already exists under "{project}" at {hours}hr',
            })
            continue

        tm = TaskMaster.objects.create(project_name=project, task_name=name, default_hours=hours)
        created.append(TaskMasterSerializer(tm).data)

    return Response({
        "created_count": len(created),
        "skipped_count": len(skipped),
        "error_count": len(errors),
        "created": created,
        "skipped": skipped,
        "errors": errors,
    })