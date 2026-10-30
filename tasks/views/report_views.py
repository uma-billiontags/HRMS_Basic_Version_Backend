import datetime
import calendar
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from ..models import Task, TimerSession, TaskPauseLog
from accounts.models import Employee, Admin
from ..serializers import ( TaskListSerializer )
from django.db import models as db_models  # aliased so it doesn't clash with the `models` you already reference via Task etc.
from tasks.views.utils import _is_admin, _current_employee, _current_admin, _is_tl, task_list_queryset

def _calculate_month_work_capacity(year: int, month: int):
    """
    Company policy: 6 working days per week (Mon through Sat).
    Only Sunday (weekday 6) is a weekly holiday.
    Standard work hours = 8.0 hours per working day.
    Returns working days count and total available work hours for that month.
    """
    _, num_days = calendar.monthrange(year, month)
    sundays = 0
    working_days = 0
    for d in range(1, num_days + 1):
        dt = datetime.date(year, month, d)
        if dt.weekday() == 6:  # Sunday
            sundays += 1
        else:  # Monday to Saturday
            working_days += 1
    total_working_hours = round(working_days * 8.0, 2)
    return {
        "year": year,
        "month": month,
        "month_name": datetime.date(year, month, 1).strftime("%B %Y"),
        "total_days": num_days,
        "sundays": sundays,
        "working_days": working_days,
        "daily_hours": 8.0,
        "total_working_hours": total_working_hours,
    }


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def get_admin_reports(request):
    """
    GET /api/reports/admin/?employee=&status=&priority=&date_from=&date_to=
    Admin-only. Summary KPIs + breakdowns + the filtered task list itself.
    """
    if not _is_admin(request):
        return Response({"detail": "Admin only."}, status=status.HTTP_403_FORBIDDEN)

    tasks = task_list_queryset()   
    employee_id = request.query_params.get("employee")
    if employee_id:
        tasks = tasks.filter(assigned_to_id=employee_id)
    status_filter = request.query_params.get("status")
    if status_filter:
        tasks = tasks.filter(task_status=status_filter)
    priority = request.query_params.get("priority")
    if priority:
        tasks = tasks.filter(priority=priority)
    date_from = request.query_params.get("date_from")
    if date_from:
        tasks = tasks.filter(assigned_date__gte=date_from)
    date_to = request.query_params.get("date_to")
    if date_to:
        tasks = tasks.filter(assigned_date__lte=date_to)

    total = tasks.count()
    completed = tasks.filter(task_status=Task.Status.COMPLETED).count()
    overdue = tasks.filter(due_date__lt=timezone.now().date()).exclude(
        task_status__in=[Task.Status.COMPLETED, Task.Status.CANCELLED]
    ).count()
    total_hours = tasks.aggregate(total=db_models.Sum("total_time_taken"))["total"] or 0
    avg_rating = tasks.exclude(rating__isnull=True).aggregate(avg=db_models.Avg("rating"))["avg"]

    by_employee = list(
        tasks.exclude(assigned_to__isnull=True)
        .values("assigned_to__name")
        .annotate(count=db_models.Count("id"), hours=db_models.Sum("total_time_taken"))
        .order_by("-count")
    )
    by_status = list(tasks.values("task_status").annotate(count=db_models.Count("id")))

    # Monthly tracking for current month (Mon-Sat 8h/day, Sun holiday)
    now_date = timezone.localdate() if hasattr(timezone, "localdate") else timezone.now().date()
    month_cap = _calculate_month_work_capacity(now_date.year, now_date.month)
    month_start = datetime.date(now_date.year, now_date.month, 1)
    month_end = datetime.date(now_date.year, now_date.month, month_cap["total_days"])
    month_tasks = tasks.filter(
        db_models.Q(due_date__range=(month_start, month_end)) |
        db_models.Q(due_date__isnull=True, assigned_date__range=(month_start, month_end))
    )
    month_assigned_hours = float(month_tasks.aggregate(t=db_models.Sum("allotted_time"))["t"] or 0)
    month_capacity_percentage = round((month_assigned_hours / month_cap["total_working_hours"]) * 100, 1) if month_cap["total_working_hours"] else 0

    return Response({
        "summary": {
            "total_tasks": total,
            "completed": completed,
            "completion_rate": round((completed / total) * 100, 1) if total else 0,
            "overdue": overdue,
            "total_hours": float(total_hours),
            "avg_rating": round(avg_rating, 2) if avg_rating else None,
            "monthly_assigned_hours": round(month_assigned_hours, 2),
            "monthly_working_hours": month_cap["total_working_hours"],
            "monthly_working_days": month_cap["working_days"],
            "monthly_capacity_percentage": month_capacity_percentage,
            "month_name": month_cap["month_name"],
        },
        "by_employee": by_employee,
        "by_status": by_status,
        "tasks": TaskListSerializer(tasks.order_by("-assigned_date"), many=True).data,
    })
    
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def get_my_reports(request):
    """GET /api/reports/mine/ — employee-only personal summary."""
    employee = _current_employee(request)
    if employee is None:
        return Response({"detail": "Employees only."}, status=status.HTTP_403_FORBIDDEN)

    tasks = task_list_queryset().filter(assigned_to=employee) 
    total = tasks.count()
    completed = tasks.filter(task_status=Task.Status.COMPLETED).count()
    total_hours = tasks.aggregate(total=db_models.Sum("total_time_taken"))["total"] or 0
    avg_rating = tasks.exclude(rating__isnull=True).aggregate(avg=db_models.Avg("rating"))["avg"]
    by_quality = list(
        tasks.exclude(quality_of_task="").values("quality_of_task").annotate(count=db_models.Count("id"))
    )

    # Monthly tracking for logged-in employee (Mon-Sat 8h/day, Sun holiday)
    now_date = timezone.localdate() if hasattr(timezone, "localdate") else timezone.now().date()
    month_cap = _calculate_month_work_capacity(now_date.year, now_date.month)
    month_start = datetime.date(now_date.year, now_date.month, 1)
    month_end = datetime.date(now_date.year, now_date.month, month_cap["total_days"])
    month_tasks = tasks.filter(
        db_models.Q(due_date__range=(month_start, month_end)) |
        db_models.Q(due_date__isnull=True, assigned_date__range=(month_start, month_end))
    )
    month_assigned_hours = float(month_tasks.aggregate(t=db_models.Sum("allotted_time"))["t"] or 0)
    month_capacity_percentage = round((month_assigned_hours / month_cap["total_working_hours"]) * 100, 1) if month_cap["total_working_hours"] else 0
    
    return Response({
        "summary": {
            "total_tasks": total,
            "completed": completed,
            "completion_rate": round((completed / total) * 100, 1) if total else 0,
            "total_hours": float(total_hours),
            "avg_rating": round(avg_rating, 2) if avg_rating else None,
            "monthly_assigned_hours": round(month_assigned_hours, 2),
            "monthly_working_hours": month_cap["total_working_hours"],
            "monthly_working_days": month_cap["working_days"],
            "monthly_capacity_percentage": month_capacity_percentage,
            "month_name": month_cap["month_name"],
        },
        "by_quality": by_quality,
        "tasks": TaskListSerializer(tasks.order_by("-assigned_date"), many=True).data,
    })
    

# tasks/views/reports.py — add these imports at the top
from django.db.models.functions import TruncMonth
from django.utils import timezone


def _months_ago(date, n):
    """No dateutil dependency — walk back n months from the 1st of `date`'s month."""
    year, month = date.year, date.month - n
    while month <= 0:
        month += 12
        year -= 1
    return date.replace(year=year, month=month, day=1)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def get_employee_rating_trends(request):
    """
    GET /api/tasks/reports/rating_trends/?months=6
    Admin-only. Monthly average rating per employee, based on reviewed_date
    (when the task was actually rated during approval). Only counts tasks
    that have gone through review and received a rating — everything else
    is excluded, so an employee with zero rated tasks in a month just
    doesn't appear for that month (not shown as 0).
    """
    if not _is_admin(request):
        return Response({"detail": "Admin only."}, status=status.HTTP_403_FORBIDDEN)

    months = int(request.query_params.get("months", 6))
    since = _months_ago(timezone.now().date(), months - 1)

    rated_tasks = Task.objects.filter(
        rating__isnull=False,
        reviewed_date__date__gte=since,
    ).annotate(month=TruncMonth("reviewed_date"))

    rows = (
        rated_tasks.values("assigned_to_id", "assigned_to__name", "month")
        .annotate(avg_rating=db_models.Avg("rating"), task_count=db_models.Count("id"))
        .order_by("assigned_to__name", "month")
    )

    by_employee = {}
    for r in rows:
        emp_id = r["assigned_to_id"]
        if emp_id not in by_employee:
            by_employee[emp_id] = {
                "employee_id": emp_id,
                "employee_name": r["assigned_to__name"],
                "months": [],
            }
        by_employee[emp_id]["months"].append({
            "month": r["month"].strftime("%Y-%m"),
            "avg_rating": round(r["avg_rating"], 2),
            "task_count": r["task_count"],
        })

    overall_rows = (
        rated_tasks.values("month")
        .annotate(avg_rating=db_models.Avg("rating"), task_count=db_models.Count("id"))
        .order_by("month")
    )
    overall_trend = [
        {"month": r["month"].strftime("%Y-%m"), "avg_rating": round(r["avg_rating"], 2), "task_count": r["task_count"]}
        for r in overall_rows
    ]

    return Response({
        "months_requested": months,
        "employees": sorted(
            by_employee.values(),
            key=lambda e: e["months"][-1]["avg_rating"] if e["months"] else 0,
            reverse=True,
        ),
        "overall_trend": overall_trend,
    })


def _parse_target_date(date_str):
    if date_str:
        try:
            return datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            pass
    return timezone.localdate() if hasattr(timezone, "localdate") else timezone.now().date()


def _get_utc_day_bounds(target_date, tz_offset_str):
    try:
        offset_minutes = int(tz_offset_str) if tz_offset_str is not None else -330
    except (TypeError, ValueError):
        offset_minutes = -330  # Default to India Standard Time (+05:30)

    local_start = datetime.datetime.combine(target_date, datetime.time.min)
    local_end = datetime.datetime.combine(target_date, datetime.time.max)
    utc_start = timezone.make_aware(local_start + datetime.timedelta(minutes=offset_minutes), datetime.timezone.utc)
    utc_end = timezone.make_aware(local_end + datetime.timedelta(minutes=offset_minutes), datetime.timezone.utc)
    return utc_start, utc_end


def _build_day_timeline_for_actor(actor, is_admin_actor, target_date, utc_start, utc_end):
    now = timezone.now()

    if is_admin_actor:
        sessions = TimerSession.objects.filter(
            admin=actor,
            start_time__gte=utc_start,
            start_time__lte=utc_end
        ).select_related("task").order_by("start_time")
        pauses = TaskPauseLog.objects.filter(
            admin=actor,
            start_time__gte=utc_start,
            start_time__lte=utc_end
        ).select_related("task").order_by("start_time")
        actor_id = actor.id
        actor_name = actor.name
        department = "Admin"
    else:
        sessions = TimerSession.objects.filter(
            employee=actor,
            start_time__gte=utc_start,
            start_time__lte=utc_end
        ).select_related("task").order_by("start_time")
        pauses = TaskPauseLog.objects.filter(
            employee=actor,
            start_time__gte=utc_start,
            start_time__lte=utc_end
        ).select_related("task").order_by("start_time")
        actor_id = actor.id
        actor_name = actor.name
        department = actor.department or ""

    session_events = []
    for s in sessions:
        end = s.end_time or now
        dur = s.duration_seconds if s.duration_seconds is not None else int((end - s.start_time).total_seconds())
        dur = max(0, dur)
        session_events.append({
            "id": f"work-{s.id}",
            "type": "work",
            "task_id": s.task_id,
            "task_name": s.task.task_name,
            "start_time": s.start_time.isoformat(),
            "end_time": end.isoformat(),
            "duration_seconds": dur,
            "duration_minutes": round(dur / 60.0, 1),
            "label": s.task.task_name,
            "is_active": s.end_time is None,
        })

    pause_events = []
    for p in pauses:
        end = p.end_time or now
        dur = p.duration_seconds if p.duration_seconds is not None else int((end - p.start_time).total_seconds())
        dur = max(0, dur)
        label = p.reason if p.reason else ("Break" if p.pause_type == TaskPauseLog.PauseType.BREAK else "Pause")
        pause_events.append({
            "id": f"pause-{p.id}",
            "type": p.pause_type,
            "break_option": p.break_option,
            "break_duration_minutes": p.break_duration_minutes,
            "reason": p.reason,
            "task_id": p.task_id,
            "task_name": p.task.task_name,
            "start_time": p.start_time.isoformat(),
            "end_time": end.isoformat(),
            "duration_seconds": dur,
            "duration_minutes": round(dur / 60.0, 1),
            "label": label,
            "is_active": p.end_time is None,
        })

    all_events = sorted(session_events + pause_events, key=lambda x: x["start_time"])

    total_work_seconds = sum(e["duration_seconds"] for e in session_events)
    total_break_seconds = sum(e["duration_seconds"] for e in pause_events if e["type"] == "break")
    total_another_reason_seconds = sum(e["duration_seconds"] for e in pause_events if e["type"] == "another_reason")

    work_hours = round(total_work_seconds / 3600.0, 2)
    break_hours = round(total_break_seconds / 3600.0, 2)
    another_reason_hours = round(total_another_reason_seconds / 3600.0, 2)
    total_logged_hours = round(work_hours + break_hours + another_reason_hours, 2)
    untracked_hours = max(0.0, round(9.0 - total_logged_hours, 2))

    return {
        "date": target_date.strftime("%Y-%m-%d"),
        "day_name": target_date.strftime("%a, %d %b"),
        "actor_id": actor_id,
        "actor_name": actor_name,
        "is_admin": is_admin_actor,
        "department": department,
        "work_hours": work_hours,
        "work_minutes": int(round(total_work_seconds / 60)),
        "break_hours": break_hours,
        "break_minutes": int(round(total_break_seconds / 60)),
        "another_reason_hours": another_reason_hours,
        "another_reason_minutes": int(round(total_another_reason_seconds / 60)),
        "untracked_hours": untracked_hours,
        "untracked_minutes": int(round(untracked_hours * 60)),
        "total_logged_hours": total_logged_hours,
        "work_target_met": total_work_seconds >= (8 * 3600),
        "break_exceeded": total_break_seconds > 3600,
        "break_allowance_remaining_minutes": max(0, 60 - int(round(total_break_seconds / 60))),
        "events": all_events,
    }


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def get_daily_timeline(request):
    """
    GET /api/tasks/reports/daily_timeline/?date=YYYY-MM-DD&employee_id=&mode=day|week&tz_offset=-330
    Visual Work & Break timeline report for stacked bar charts and timeline ribbons.
    """
    is_admin = _is_admin(request)
    current_emp = _current_employee(request)
    current_adm = _current_admin(request)

    if not is_admin and current_emp is None:
        return Response({"detail": "Not authorized."}, status=status.HTTP_403_FORBIDDEN)

    target_date = _parse_target_date(request.query_params.get("date"))
    tz_offset_str = request.query_params.get("tz_offset")
    mode = request.query_params.get("mode", "day")
    employee_param = request.query_params.get("employee_id")

    office_schedule = {
        "start_time": "09:30",
        "end_time": "18:30",
        "total_office_hours": 9.0,
        "target_work_hours": 8.0,
        "break_allowance_hours": 1.0,
        "standard_breaks": [
            {"name": "Morning Refreshment", "start": "10:45", "end": "11:00", "duration_minutes": 15},
            {"name": "Lunch Break", "start": "13:30", "end": "14:00", "alt_start": "14:00", "alt_end": "14:30", "duration_minutes": 30},
            {"name": "Evening Refreshment", "start": "16:45", "end": "17:00", "duration_minutes": 15},
        ]
    }

    # Case 1: Employee logged in (always sees their own data, unless TL viewing their team member)
    if not is_admin:
        target_emp = current_emp
        team_members_list = []
        if _is_tl(request):
            team_members = Employee.objects.filter(belong_to_tl=current_emp, is_active=True).order_by("name")
            team_members_list = [
                {"id": current_emp.id, "name": f"{current_emp.name} (My Details)", "department": current_emp.department}
            ] + [{"id": e.id, "name": e.name, "department": e.department} for e in team_members]

            if employee_param and employee_param.isdigit():
                found = team_members.filter(pk=int(employee_param)).first()
                if found:
                    target_emp = found
                elif int(employee_param) == current_emp.id:
                    target_emp = current_emp

        if mode == "week":
            days = []
            for i in range(6, -1, -1):
                d = target_date - datetime.timedelta(days=i)
                u_start, u_end = _get_utc_day_bounds(d, tz_offset_str)
                days.append(_build_day_timeline_for_actor(target_emp, False, d, u_start, u_end))
            return Response({
                "view_type": "week",
                "employee": {"id": target_emp.id, "name": target_emp.name, "department": target_emp.department},
                "office_schedule": office_schedule,
                "days": days,
                "current_day": days[-1],
                "team_members": team_members_list,
            })
        else:
            u_start, u_end = _get_utc_day_bounds(target_date, tz_offset_str)
            day_data = _build_day_timeline_for_actor(target_emp, False, target_date, u_start, u_end)
            return Response({
                "view_type": "day",
                "employee": {"id": target_emp.id, "name": target_emp.name, "department": target_emp.department},
                "office_schedule": office_schedule,
                "data": day_data,
                "team_members": team_members_list,
            })

    # Case 2: Admin logged in
    # If admin specified a single employee
    if employee_param and employee_param.isdigit():
        emp = get_object_or_404(Employee, pk=int(employee_param))
        if mode == "week":
            days = []
            for i in range(6, -1, -1):
                d = target_date - datetime.timedelta(days=i)
                u_start, u_end = _get_utc_day_bounds(d, tz_offset_str)
                days.append(_build_day_timeline_for_actor(emp, False, d, u_start, u_end))
            return Response({
                "view_type": "week",
                "employee": {"id": emp.id, "name": emp.name, "department": emp.department},
                "office_schedule": office_schedule,
                "days": days,
                "current_day": days[-1],
            })
        else:
            u_start, u_end = _get_utc_day_bounds(target_date, tz_offset_str)
            day_data = _build_day_timeline_for_actor(emp, False, target_date, u_start, u_end)
            return Response({
                "view_type": "day",
                "employee": {"id": emp.id, "name": emp.name, "department": emp.department},
                "office_schedule": office_schedule,
                "data": day_data,
            })

    # If admin viewing their own admin sessions
    if employee_param == "admin" and current_adm:
        u_start, u_end = _get_utc_day_bounds(target_date, tz_offset_str)
        day_data = _build_day_timeline_for_actor(current_adm, True, target_date, u_start, u_end)
        return Response({
            "view_type": "day",
            "employee": {"id": current_adm.id, "name": current_adm.name, "department": "Admin"},
            "office_schedule": office_schedule,
            "data": day_data,
        })

    # Admin viewing ALL employees for target_date (Horizontal Stacked Bar across employees!)
    u_start, u_end = _get_utc_day_bounds(target_date, tz_offset_str)
    active_employees = Employee.objects.filter(is_active=True).order_by("name")
    employees_data = [
        _build_day_timeline_for_actor(emp, False, target_date, u_start, u_end)
        for emp in active_employees
    ]

    return Response({
        "view_type": "all_employees",
        "date": target_date.strftime("%Y-%m-%d"),
        "office_schedule": office_schedule,
        "employees": employees_data,
    })


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def get_assigned_hours_tracking(request):
    """
    GET /api/tasks/reports/assigned_hours_tracking/?week_date=YYYY-MM-DD&employee_id=&year=&month=
    Tracks:
    1. Monthly work hours and assigned task hours based on days in the month:
       - 6 working days/week (Monday to Saturday, 8h/day)
       - Sunday is weekly holiday (0h)
    2. Weekly breakdown across 7 days (Monday to Sunday):
       - Each day's assigned hours out of standard 8.0 hrs
       - Sunday marked as holiday (0h target)
       - Day-by-day task lists and overtime/under-allocation tracking
       - Week navigation support (any week date passed in query params)
    """
    is_admin = _is_admin(request)
    current_emp = _current_employee(request)
    current_adm = _current_admin(request)

    if not is_admin and current_emp is None:
        return Response({"detail": "Not authorized."}, status=status.HTTP_403_FORBIDDEN)

    week_date_str = request.query_params.get("week_date")
    target_date = _parse_target_date(week_date_str)

    # Determine year and month from query params or target_date
    try:
        req_year = int(request.query_params.get("year", target_date.year))
    except (TypeError, ValueError):
        req_year = target_date.year

    try:
        req_month = int(request.query_params.get("month", target_date.month))
    except (TypeError, ValueError):
        req_month = target_date.month

    # Base task queryset and actor information
    employee_param = request.query_params.get("employee_id")
    actor_id = None
    actor_name = "All Employees"
    actor_role = "all"

    employees_list = []
    is_tl_user = False
    if not is_admin:
        if _is_tl(request):
            is_tl_user = True
            team_members = Employee.objects.filter(belong_to_tl=current_emp, is_active=True).order_by("name")
            target_emp = current_emp
            if employee_param and employee_param.isdigit():
                found = team_members.filter(pk=int(employee_param)).first()
                if found:
                    target_emp = found
                elif int(employee_param) == current_emp.id:
                    target_emp = current_emp

            base_tasks = task_list_queryset().filter(assigned_to=target_emp)
            actor_id = target_emp.id
            actor_name = target_emp.name
            actor_role = "employee"
            employees_list = [
                {"id": current_emp.id, "name": f"{current_emp.name} (My Details)", "department": current_emp.department}
            ] + [{"id": e.id, "name": e.name, "department": e.department} for e in team_members]
        else:
            # Regular employee viewing their own
            base_tasks = task_list_queryset().filter(assigned_to=current_emp)
            actor_id = current_emp.id
            actor_name = current_emp.name
            actor_role = "employee"
    else:
        # Admin
        employees_list = [
            {"id": e.id, "name": e.name, "department": e.department}
            for e in Employee.objects.filter(is_active=True).order_by("name")
        ]
        if employee_param and employee_param.isdigit():
            emp = get_object_or_404(Employee, pk=int(employee_param))
            base_tasks = task_list_queryset().filter(assigned_to=emp)
            actor_id = emp.id
            actor_name = emp.name
            actor_role = "employee"
        elif employee_param == "admin" and current_adm:
            base_tasks = task_list_queryset().filter(assigned_to_admin=current_adm)
            actor_id = current_adm.id
            actor_name = current_adm.name
            actor_role = "admin"
        else:
            base_tasks = task_list_queryset()
            actor_id = "all"
            actor_name = "All Employees"
            actor_role = "all"

    # Active employees count for computing team averages
    active_employees_count = max(1, Employee.objects.filter(is_active=True).count()) if is_admin else 1
    is_aggregate = (actor_role == "all")

    # 1. Month tracking
    month_cap = _calculate_month_work_capacity(req_year, req_month)
    month_start = datetime.date(req_year, req_month, 1)
    month_end = datetime.date(req_year, req_month, month_cap["total_days"])

    month_tasks = base_tasks.filter(
        db_models.Q(due_date__range=(month_start, month_end)) |
        db_models.Q(due_date__isnull=True, assigned_date__range=(month_start, month_end))
    )
    total_month_assigned = float(month_tasks.aggregate(t=db_models.Sum("allotted_time"))["t"] or 0)
    
    single_month_hours = month_cap["total_working_hours"]
    team_month_hours = round(single_month_hours * (active_employees_count if is_aggregate else 1), 2)
    avg_month_assigned = round(total_month_assigned / (active_employees_count if is_aggregate else 1), 2)
    month_utilization = round((total_month_assigned / team_month_hours) * 100, 1) if team_month_hours else 0

    # 2. Week tracking (Monday to Sunday)
    monday = target_date - datetime.timedelta(days=target_date.weekday())
    sunday = monday + datetime.timedelta(days=6)

    week_tasks = list(
        base_tasks.filter(
            db_models.Q(due_date__range=(monday, sunday)) |
            db_models.Q(due_date__isnull=True, assigned_date__range=(monday, sunday))
        ).select_related("assigned_to", "assigned_to_admin")
    )

    days = []
    for i in range(7):
        day_date = monday + datetime.timedelta(days=i)
        is_sunday = (day_date.weekday() == 6)
        target_work_hours = 0.0 if is_sunday else 8.0
        team_target_hours = 0.0 if is_sunday else round(8.0 * (active_employees_count if is_aggregate else 1), 2)

        # Tasks for this day (due_date == day_date, or due_date is null and assigned_date == day_date)
        day_tasks = [
            t for t in week_tasks
            if (t.due_date == day_date) or (t.due_date is None and t.assigned_date == day_date)
        ]
        assigned_hours = round(sum(float(t.allotted_time or 0) for t in day_tasks), 2)
        avg_assigned_hours = round(assigned_hours / (active_employees_count if is_aggregate else 1), 2)

        available_hours = round(max(0.0, target_work_hours - (avg_assigned_hours if is_aggregate else assigned_hours)), 2)
        overtime_hours = round(max(0.0, (avg_assigned_hours if is_aggregate else assigned_hours) - target_work_hours), 2)

        days.append({
            "date": day_date.strftime("%Y-%m-%d"),
            "day_name": day_date.strftime("%a"),
            "full_day_name": day_date.strftime("%A"),
            "formatted_date": day_date.strftime("%a, %d %b"),
            "is_working_day": not is_sunday,
            "is_sunday": is_sunday,
            "target_work_hours": target_work_hours,
            "team_target_work_hours": team_target_hours,
            "assigned_hours": assigned_hours,
            "average_assigned_hours": avg_assigned_hours,
            "available_hours": available_hours,
            "overtime_hours": overtime_hours,
            "task_count": len(day_tasks),
            "tasks": [
                {
                    "id": t.id,
                    "task_id": t.task_id,
                    "sub_task_id": t.sub_task_id,
                    "task_name": t.task_name,
                    "allotted_time": float(t.allotted_time or 0),
                    "assignee_name": t.assignee_name,
                    "priority": t.priority,
                    "status": t.task_status,
                }
                for t in day_tasks
            ],
        })

    total_week_assigned = round(sum(d["assigned_hours"] for d in days), 2)
    single_week_hours = 48.0  # 6 working days * 8h
    team_week_hours = round(48.0 * (active_employees_count if is_aggregate else 1), 2)
    avg_week_assigned = round(total_week_assigned / (active_employees_count if is_aggregate else 1), 2)
    week_utilization = round((total_week_assigned / team_week_hours) * 100, 1) if team_week_hours else 0

    if is_admin:
        employees_list = list(
            Employee.objects.filter(is_active=True).values("id", "name", "department").order_by("name")
        )
    elif not is_tl_user:
        employees_list = []

    return Response({
        "actor": {
            "name": actor_name,
            "role": actor_role,
            "id": actor_id,
            "is_aggregate": is_aggregate,
            "active_employees_count": active_employees_count,
        },
        "month_tracking": {
            "year": req_year,
            "month": req_month,
            "month_name": month_cap["month_name"],
            "total_days": month_cap["total_days"],
            "sundays_count": month_cap["sundays"],
            "working_days": month_cap["working_days"],
            "daily_work_hours": 8.0,
            "total_working_hours": single_month_hours,
            "total_team_working_hours": team_month_hours,
            "assigned_hours": round(total_month_assigned, 2),
            "average_assigned_hours": avg_month_assigned,
            "capacity_percentage": month_utilization,
            "remaining_hours": round(max(0.0, team_month_hours - total_month_assigned), 2),
            "task_count": month_tasks.count(),
        },
        "week_tracking": {
            "week_start": monday.strftime("%Y-%m-%d"),
            "week_end": sunday.strftime("%Y-%m-%d"),
            "week_label": f"{monday.strftime('%d %b')} - {sunday.strftime('%d %b %Y')}",
            "week_number": monday.isocalendar()[1],
            "total_week_working_days": 6,
            "total_week_working_hours": single_week_hours,
            "total_team_week_hours": team_week_hours,
            "total_week_assigned_hours": total_week_assigned,
            "average_week_assigned_hours": avg_week_assigned,
            "week_capacity_percentage": week_utilization,
            "days": days,
        },
        "employees": employees_list,
        "is_tl": is_tl_user,
    })


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def get_monthly_performance_report(request):
    """
    GET /api/tasks/reports/monthly_performance/?year=YYYY&employee_id=ID
    Performance module:
    - Calculates monthly task metrics (Target 200h, Assigned Hours, Working/Completed Hours,
      Avg Rating, Reworks, On-Time %, Monthly Target Met).
    - Closed-month restriction: ONLY shows months that have already concluded before today.
    - Role Scoping:
      * Admin: Can filter and view all active employees + admins.
      * TL: Defaults to TL themselves ("My Details"), with dropdown options for their team members.
      * Regular Employee: Strictly limited to their own record.
    """
    is_admin = _is_admin(request)
    current_emp = _current_employee(request)
    current_adm = _current_admin(request)

    if not is_admin and current_emp is None:
        return Response({"detail": "Not authorized."}, status=status.HTTP_403_FORBIDDEN)

    now_date = timezone.localdate() if hasattr(timezone, "localdate") else timezone.now().date()
    
    # Determine year
    req_year_str = request.query_params.get("year")
    try:
        req_year = int(req_year_str) if req_year_str else now_date.year
    except ValueError:
        req_year = now_date.year

    employee_param = request.query_params.get("employee_id")
    is_tl_user = _is_tl(request) if not is_admin else False

    target_emp = None
    target_admin = None
    employees_dropdown = []

    if is_admin:
        all_emps = Employee.objects.filter(is_active=True).order_by("name")
        employees_dropdown = [
            {
                "id": e.id,
                "name": e.name,
                "role": e.role,
                "department": e.department,
                "employee_id": e.employee_id or "",
                "is_admin": False,
            }
            for e in all_emps
        ]
        # Admin can also inspect admin accounts if any have tasks
        admins = Admin.objects.filter(is_active=True).order_by("name")
        for a in admins:
            employees_dropdown.append({
                "id": f"admin_{a.id}",
                "name": f"{a.name} (Admin)",
                "role": "Admin",
                "department": "Admin",
                "employee_id": f"ADM-{a.id}",
                "is_admin": True,
            })

        if employee_param:
            if str(employee_param).startswith("admin_"):
                adm_id = int(str(employee_param).replace("admin_", ""))
                target_admin = admins.filter(pk=adm_id).first()
            elif str(employee_param).isdigit():
                target_emp = all_emps.filter(pk=int(employee_param)).first()
        
        # If no target chosen or not found, default to first employee or first admin
        if not target_emp and not target_admin:
            target_emp = all_emps.first()
            if not target_emp and admins.exists():
                target_admin = admins.first()

    elif is_tl_user:
        team_members = Employee.objects.filter(belong_to_tl=current_emp, is_active=True).order_by("name")
        employees_dropdown = [
            {
                "id": current_emp.id,
                "name": f"{current_emp.name} (My Details)",
                "role": current_emp.role,
                "department": current_emp.department,
                "employee_id": current_emp.employee_id or "",
                "is_admin": False,
            }
        ] + [
            {
                "id": e.id,
                "name": e.name,
                "role": e.role,
                "department": e.department,
                "employee_id": e.employee_id or "",
                "is_admin": False,
            }
            for e in team_members
        ]

        target_emp = current_emp  # Default to TL's own data
        if employee_param and str(employee_param).isdigit():
            requested_id = int(employee_param)
            if requested_id == current_emp.id:
                target_emp = current_emp
            else:
                found = team_members.filter(pk=requested_id).first()
                if found:
                    target_emp = found

    else:
        # Regular employee: strictly their own data, no dropdown
        target_emp = current_emp
        employees_dropdown = []

    # Target info for the response
    if target_emp:
        target_info = {
            "id": target_emp.id,
            "name": target_emp.name,
            "role": target_emp.role,
            "department": target_emp.department or "",
            "employee_id": target_emp.employee_id or "",
            "is_admin": False,
        }
    elif target_admin:
        target_info = {
            "id": f"admin_{target_admin.id}",
            "name": target_admin.name,
            "role": "Admin",
            "department": "Admin",
            "employee_id": f"ADM-{target_admin.id}",
            "is_admin": True,
        }
    else:
        return Response({"detail": "Target user not found."}, status=status.HTTP_404_NOT_FOUND)

    TARGET_HOURS = 200.0
    closed_months = []

    # Months 1 to 12 in the requested year
    for m in range(1, 13):
        _, num_days = calendar.monthrange(req_year, m)
        m_start = datetime.date(req_year, m, 1)
        m_end = datetime.date(req_year, m, num_days)

        # STRICT MONTH-END CHECK: Only show if the month has completely ended!
        if m_end >= now_date:
            continue

        # Assigned tasks
        if target_emp:
            assigned_qs = Task.objects.filter(
                assigned_to=target_emp,
                assigned_date__range=(m_start, m_end)
            ).exclude(task_status=Task.Status.CANCELLED)

            completed_qs = Task.objects.filter(
                assigned_to=target_emp,
                task_status=Task.Status.COMPLETED
            ).filter(
                db_models.Q(reviewed_date__date__range=(m_start, m_end)) |
                db_models.Q(reviewed_date__isnull=True, submitted_date__date__range=(m_start, m_end)) |
                db_models.Q(reviewed_date__isnull=True, submitted_date__isnull=True, assigned_date__range=(m_start, m_end))
            )

            # Timer sessions
            u_start = timezone.make_aware(datetime.datetime.combine(m_start, datetime.time.min))
            u_end = timezone.make_aware(datetime.datetime.combine(m_end, datetime.time.max))
            sessions = TimerSession.objects.filter(employee=target_emp, start_time__range=(u_start, u_end))
        else:
            assigned_qs = Task.objects.filter(
                assigned_to_admin=target_admin,
                assigned_date__range=(m_start, m_end)
            ).exclude(task_status=Task.Status.CANCELLED)

            completed_qs = Task.objects.filter(
                assigned_to_admin=target_admin,
                task_status=Task.Status.COMPLETED
            ).filter(
                db_models.Q(reviewed_date__date__range=(m_start, m_end)) |
                db_models.Q(reviewed_date__isnull=True, submitted_date__date__range=(m_start, m_end)) |
                db_models.Q(reviewed_date__isnull=True, submitted_date__isnull=True, assigned_date__range=(m_start, m_end))
            )

            u_start = timezone.make_aware(datetime.datetime.combine(m_start, datetime.time.min))
            u_end = timezone.make_aware(datetime.datetime.combine(m_end, datetime.time.max))
            sessions = TimerSession.objects.filter(admin=target_admin, start_time__range=(u_start, u_end))

        assigned_hours = round(float(assigned_qs.aggregate(s=db_models.Sum("allotted_time"))["s"] or 0), 1)

        # Completed hours calculation
        completed_hours_sum = 0.0
        for t in completed_qs:
            dur = float(t.total_time_taken or 0)
            if dur <= 0 and t.allotted_time:
                dur = float(t.allotted_time)
            completed_hours_sum += dur
        completed_hours = round(completed_hours_sum, 1)

        # Logged working hours from timer sessions
        session_seconds = sessions.aggregate(s=db_models.Sum("duration_seconds"))["s"] or 0
        working_hours = round(session_seconds / 3600.0, 1)

        # Effective hours (maximum of completed task hours and logged working hours)
        effective_hours = max(completed_hours, working_hours)

        # Average Rating
        rated_tasks = completed_qs.exclude(rating__isnull=True)
        avg_rating_val = rated_tasks.aggregate(avg=db_models.Avg("rating"))["avg"]
        avg_rating = round(float(avg_rating_val), 1) if avg_rating_val is not None else None

        # Rework count
        rework_count = int(completed_qs.aggregate(s=db_models.Sum("rework_count"))["s"] or 0)

        # On-Time %
        on_time_count = 0
        total_eval_on_time = 0
        for t in completed_qs:
            if t.due_date:
                total_eval_on_time += 1
                if t.submitted_date:
                    sub_date = timezone.localtime(t.submitted_date).date() if timezone.is_aware(t.submitted_date) else t.submitted_date.date()
                    if sub_date < t.due_date:
                        on_time_count += 1
                    elif sub_date == t.due_date:
                        if not t.due_time:
                            on_time_count += 1
                        else:
                            sub_time = timezone.localtime(t.submitted_date).time() if timezone.is_aware(t.submitted_date) else t.submitted_date.time()
                            if sub_time <= t.due_time:
                                on_time_count += 1
                else:
                    on_time_count += 1
            elif t.submitted_date or t.task_status == Task.Status.COMPLETED:
                total_eval_on_time += 1
                on_time_count += 1

        on_time_pct = round((on_time_count / total_eval_on_time) * 100, 1) if total_eval_on_time > 0 else (100.0 if completed_qs.count() > 0 else None)

        # Target Met Evaluation
        if effective_hours >= 200.0:
            target_met_status = "Fully Met"
            target_met_code = "fully_met"
            is_met = True
        elif effective_hours >= 160.0:
            target_met_status = "Partially Met"
            target_met_code = "partially_met"
            is_met = True
        elif effective_hours >= 100.0:
            target_met_status = "Below Average"
            target_met_code = "below_average"
            is_met = False
        else:
            target_met_status = "Not Met"
            target_met_code = "not_met"
            is_met = False

        closed_months.append({
            "month_num": m,
            "month_name": m_start.strftime("%b %Y"),
            "full_month_name": m_start.strftime("%B %Y"),
            "target_hours": TARGET_HOURS,
            "assigned_hours": assigned_hours,
            "completed_hours": completed_hours,
            "working_hours": working_hours,
            "effective_hours": effective_hours,
            "avg_rating": avg_rating,
            "rework_count": rework_count,
            "on_time_pct": on_time_pct,
            "target_met_status": target_met_status,
            "target_met_code": target_met_code,
            "is_met": is_met,
            "completed_tasks_count": completed_qs.count(),
            "assigned_tasks_count": assigned_qs.count(),
        })

    # Summary aggregations
    num_closed = len(closed_months)
    total_assigned = round(sum(m["assigned_hours"] for m in closed_months), 1)
    total_completed = round(sum(m["effective_hours"] for m in closed_months), 1)
    fully_met_count = sum(1 for m in closed_months if m["target_met_code"] == "fully_met")
    partially_met_count = sum(1 for m in closed_months if m["target_met_code"] == "partially_met")
    below_avg_count = sum(1 for m in closed_months if m["target_met_code"] == "below_average")
    not_met_count = sum(1 for m in closed_months if m["target_met_code"] == "not_met")

    ratings_list = [m["avg_rating"] for m in closed_months if m["avg_rating"] is not None]
    overall_avg_rating = round(sum(ratings_list) / len(ratings_list), 2) if ratings_list else None

    ontime_list = [m["on_time_pct"] for m in closed_months if m["on_time_pct"] is not None]
    overall_on_time_pct = round(sum(ontime_list) / len(ontime_list), 1) if ontime_list else None

    total_reworks = sum(m["rework_count"] for m in closed_months)
    met_target_months = fully_met_count + partially_met_count
    consistency_pct = round((met_target_months / num_closed) * 100, 1) if num_closed > 0 else 0.0

    # ── Annual Performance Calculation (50% Capacity + 50% Quality) ──────
    presented_target_hours = TARGET_HOURS * num_closed
    capacity_ratio = min(1.0, total_completed / presented_target_hours) if presented_target_hours > 0 else 0.0
    capacity_score = round(capacity_ratio * 50.0, 2)

    quality_ratio = min(1.0, (overall_avg_rating / 5.0)) if overall_avg_rating is not None else 0.0
    quality_score = round(quality_ratio * 50.0, 2)

    final_score = round(capacity_score + quality_score, 2)
    score_gap = round(max(0.0, 100.0 - final_score), 2)

    if final_score >= 90.0:
        band_name = "Band A+ (Outstanding)"
        band_code = "band_a_plus"
        band_color = "#16a34a"
        hike_recommendation = "18% - 22%"
        assessment_summary = "Exceeded work capacity expectations with top-tier task quality."
    elif final_score >= 80.0:
        band_name = "Band A (Exceeds Expectations)"
        band_code = "band_a"
        band_color = "#2563eb"
        hike_recommendation = "12% - 15%"
        assessment_summary = "Consistently met capacity target with high quality ratings."
    elif final_score >= 70.0:
        band_name = "Band B (Meets Expectations)"
        band_code = "band_b"
        band_color = "#0284c7"
        hike_recommendation = "7% - 10%"
        assessment_summary = "Met standard workload and quality benchmarks."
    elif final_score >= 60.0:
        band_name = "Band C (Needs Improvement)"
        band_code = "band_c"
        band_color = "#d97706"
        hike_recommendation = "3% - 5%"
        assessment_summary = "Below capacity target or frequent rework required."
    else:
        band_name = "Band D (Unsatisfactory / PIP)"
        band_code = "band_d"
        band_color = "#dc2626"
        hike_recommendation = "0% (PIP Recommended)"
        assessment_summary = "Failed to meet minimum acceptable capacity and quality thresholds."

    annual_performance = {
        "evaluated_year": req_year,
        "evaluated_months_count": num_closed,
        "evaluated_period_label": f"Jan {req_year} - {closed_months[-1]['month_name']}" if closed_months else f"Year {req_year}",
        "presented_target_hours": presented_target_hours,
        "total_completed_hours": total_completed,
        "capacity_percentage": round(capacity_ratio * 100, 1),
        "capacity_score": capacity_score,
        "capacity_max": 50.0,
        "overall_avg_rating": overall_avg_rating,
        "quality_percentage": round(quality_ratio * 100, 1),
        "quality_score": quality_score,
        "quality_max": 50.0,
        "final_score": final_score,
        "score_gap": score_gap,
        "band_name": band_name,
        "band_code": band_code,
        "band_color": band_color,
        "hike_recommendation": hike_recommendation,
        "assessment_summary": assessment_summary,
        "consistency_percentage": consistency_pct,
        "total_reworks": total_reworks,
        "overall_on_time_pct": overall_on_time_pct,
        "score_pie_data": [
            {"name": "Capacity Score (50%)", "value": capacity_score, "color": "#3b82f6", "max": 50},
            {"name": "Quality Score (50%)", "value": quality_score, "color": "#10b981", "max": 50},
            {"name": "Gap / Deficit", "value": score_gap, "color": "#cbd5e1", "max": 100},
        ],
        "target_met_pie_data": [
            {"name": "Fully Met", "value": fully_met_count, "color": "#10b981"},
            {"name": "Partially Met", "value": partially_met_count, "color": "#3b82f6"},
            {"name": "Below Average", "value": below_avg_count, "color": "#f59e0b"},
            {"name": "Not Met", "value": not_met_count, "color": "#ef4444"},
        ]
    }

    # Available years list (e.g. current year down to past 3 years)
    available_years = [now_date.year - i for i in range(4)]

    return Response({
        "year": req_year,
        "available_years": available_years,
        "current_date": now_date.strftime("%Y-%m-%d"),
        "target_employee": target_info,
        "employees": employees_dropdown,
        "is_tl": is_tl_user,
        "is_admin": is_admin,
        "closed_months": closed_months,
        "summary": {
            "target_hours_per_month": TARGET_HOURS,
            "total_closed_months": num_closed,
            "total_target_hours": round(TARGET_HOURS * num_closed, 1),
            "total_assigned_hours": total_assigned,
            "total_completed_hours": total_completed,
            "fully_met_count": fully_met_count,
            "partially_met_count": partially_met_count,
            "below_avg_count": below_avg_count,
            "not_met_count": not_met_count,
            "met_target_months": met_target_months,
            "consistency_text": f"{met_target_months} out of {num_closed} months target met ({consistency_pct}% Consistency)" if num_closed else "No closed months evaluated yet",
            "consistency_percentage": consistency_pct,
            "overall_avg_rating": overall_avg_rating,
            "overall_on_time_pct": overall_on_time_pct,
            "total_reworks": total_reworks,
        },
        "annual_performance": annual_performance,
    })
