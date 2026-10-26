# Not endpoints — internal helpers reused everywhere. These should be imported by every other file, so they belong in a shared module.

def _is_admin(request):
    # request.user is a SimplePrincipal wrapping either Admin or Employee
    # (see accounts/authentication.py) — .role is "admin" or "employee".
    return getattr(request.user, "role", None) == "admin"


def _current_employee(request):
    """Returns the logged-in Employee, or None if the caller isn't an employee."""
    if getattr(request.user, "role", None) != "employee":
        return None
    return request.user.instance

def _current_admin(request):
    if getattr(request.user, "role", None) != "admin":
        return None
    return request.user.instance

def _current_task_actor(request):
    """('employee', instance) or ('admin', instance) or (None, None) — whoever's logged in."""
    employee = _current_employee(request)
    if employee is not None:
        return "employee", employee
    admin = _current_admin(request)
    if admin is not None:
        return "admin", admin
    return None, None

def _is_task_assignee(request, task):
    """True if the logged-in principal is who this task is actually assigned to."""
    kind, actor = _current_task_actor(request)
    if kind == "employee":
        return task.assigned_to_id == actor.id
    if kind == "admin":
        return task.assigned_to_admin_id == actor.id
    return False

def _is_tl(request):
    employee = _current_employee(request)
    return employee is not None and bool(employee.role and employee.role.strip().upper() in ["TL", "TEAM LEAD"])

def _can_manage_tasks(request):
    """Who's allowed to create tasks at all: Admin or a TL."""
    return _is_admin(request) or _is_tl(request)

def _owns_task_for_management(request, task):
    """Who's allowed to assign/reassign/hold/cancel THIS specific task.
    Admin: any task. TL: only tasks they personally created."""
    if _is_admin(request):
        return True
    employee = _current_employee(request)
    return (
        employee is not None
        and bool(employee.role and employee.role.strip().upper() in ["TL", "TEAM LEAD"])
        and task.assigned_by_employee_id == employee.id
    )

def _can_review_task(request, task):
    """
    Who's allowed to review THIS task:
    - Admin: always.
    - TL: allowed if:
      1) They created it AND it isn't assigned to themselves (self-assigned TL tasks go to admin).
      2) OR Admin created it and the assigned employee belongs to this TL.
    """
    if _is_admin(request):
        return True
    employee = _current_employee(request)
    if employee is None or not (employee.role and employee.role.strip().upper() in ["TL", "TEAM LEAD"]):
        return False

    # Case 1: TL created it (and didn't self-assign)
    if task.assigned_by_employee_id == employee.id:
        return task.assigned_to_id != employee.id

    # Case 2: Admin created it and the assigned employee reports to this TL
    if task.assigned_by_admin_id and task.assigned_to:
        return getattr(task.assigned_to, "belong_to_tl_id", None) == employee.id

    return False


def timezone_now():
    from django.utils import timezone
    return timezone.now()

# tasks/views/utils.py — add this

def task_list_queryset():
    """
    Base queryset for every view that returns TaskListSerializer output.
    Kills the N+1s that TaskListSerializer's SerializerMethodFields cause:
    assignee_name/role, department_name, assigned_by_name, has_active_session,
    attachments — each would otherwise be a separate query per row.
    """
    from django.db.models import Exists, OuterRef
    from ..models import Task, TimerSession

    return (
        Task.objects
        .select_related("assigned_to", "assigned_to__belong_to_tl", "assigned_to_admin", "assigned_by_admin", "assigned_by_employee")
        .prefetch_related("attachments")
        .annotate(
            _has_active_session=Exists(
                TimerSession.objects.filter(task=OuterRef("pk"), end_time__isnull=True)
            )
        )
    )
    
from django.http import JsonResponse
from ..models import Task

def health_check(request):
    Task.objects.exists()   # tiny DB query — keeps Supabase awake too
    return JsonResponse({"status": "ok"})