# Activity / Audit Log
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from tasks.views.utils import _is_admin, _current_employee
from rest_framework import status

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def get_all_activity(request):
    """
    GET /api/activity/?task_id=&action=&employee=
    Admin-only. Sitewide audit log, most recent first, capped at 500 rows.
    """
    if not _is_admin(request):
        return Response({"detail": "Admin only."}, status=status.HTTP_403_FORBIDDEN)

    from ..activity import ActivityLog
    logs = ActivityLog.objects.select_related(
        "task", "actor_admin", "actor_employee"
    ).order_by("-created_at")

    task_id = request.query_params.get("task_id")
    if task_id:
        logs = logs.filter(task__task_id__icontains=task_id)
    action = request.query_params.get("action")
    if action:
        logs = logs.filter(action=action)
    employee_id = request.query_params.get("employee")
    if employee_id:
        logs = logs.filter(actor_employee_id=employee_id)

    return Response([_activity_to_dict(l) for l in logs[:500]])

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def get_my_activity(request):
    """
    GET /api/my_activity/
    Employee-only. Every activity entry on tasks assigned to them —
    including admin actions like approve/rework, so they can see their
    task's full history, not just their own clicks.
    """
    employee = _current_employee(request)
    if employee is None:
        return Response({"detail": "Employees only."}, status=status.HTTP_403_FORBIDDEN)

    from ..activity import ActivityLog
    logs = ActivityLog.objects.filter(task__assigned_to=employee).select_related(
        "task", "actor_admin", "actor_employee"
    ).order_by("-created_at")

    return Response([_activity_to_dict(l) for l in logs[:500]])

def _activity_to_dict(log):
    return {
        "id": log.id,
        "sub_task_id": log.task.sub_task_id,
        "task_id": log.task.task_id,
        "task_name": log.task.task_name,
        "actor_name": log.actor_name,
        "actor_role": "admin" if log.actor_admin_id else ("employee" if log.actor_employee_id else "system"),
        "action": log.action,
        "action_label": log.get_action_display(),
        "from_status": log.from_status,
        "to_status": log.to_status,
        "details": log.details,
        "created_at": log.created_at,
    }
    
