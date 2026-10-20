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
from tasks.views.utils import _is_admin, _current_employee, _current_admin, task_list_queryset

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

    # Case 1: Employee logged in (always sees their own data)
    if not is_admin:
        if mode == "week":
            days = []
            for i in range(6, -1, -1):
                d = target_date - datetime.timedelta(days=i)
                u_start, u_end = _get_utc_day_bounds(d, tz_offset_str)
                days.append(_build_day_timeline_for_actor(current_emp, False, d, u_start, u_end))
            return Response({
                "view_type": "week",
                "employee": {"id": current_emp.id, "name": current_emp.name, "department": current_emp.department},
                "office_schedule": office_schedule,
                "days": days,
                "current_day": days[-1],
            })
        else:
            u_start, u_end = _get_utc_day_bounds(target_date, tz_offset_str)
            day_data = _build_day_timeline_for_actor(current_emp, False, target_date, u_start, u_end)
            return Response({
                "view_type": "day",
                "employee": {"id": current_emp.id, "name": current_emp.name, "department": current_emp.department},
                "office_schedule": office_schedule,
                "data": day_data,
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

    if not is_admin:
        # Regular employee / TL viewing their own
        base_tasks = task_list_queryset().filter(assigned_to=current_emp)
        actor_id = current_emp.id
        actor_name = current_emp.name
        actor_role = "employee"
    else:
        # Admin
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

    employees_list = (
        list(Employee.objects.filter(is_active=True).values("id", "name", "department").order_by("name"))
        if is_admin else []
    )

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
    })