# Reports

from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from ..models import Task
from ..serializers import ( TaskListSerializer )
from django.db import models as db_models  # aliased so it doesn't clash with the `models` you already reference via Task etc.
from tasks.views.utils import _is_admin, _current_employee

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def get_admin_reports(request):
    """
    GET /api/reports/admin/?employee=&status=&priority=&date_from=&date_to=
    Admin-only. Summary KPIs + breakdowns + the filtered task list itself.
    """
    if not _is_admin(request):
        return Response({"detail": "Admin only."}, status=status.HTTP_403_FORBIDDEN)

    tasks = Task.objects.all()
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

    return Response({
        "summary": {
            "total_tasks": total,
            "completed": completed,
            "completion_rate": round((completed / total) * 100, 1) if total else 0,
            "overdue": overdue,
            "total_hours": float(total_hours),
            "avg_rating": round(avg_rating, 2) if avg_rating else None,
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

    tasks = Task.objects.filter(assigned_to=employee)
    total = tasks.count()
    completed = tasks.filter(task_status=Task.Status.COMPLETED).count()
    total_hours = tasks.aggregate(total=db_models.Sum("total_time_taken"))["total"] or 0
    avg_rating = tasks.exclude(rating__isnull=True).aggregate(avg=db_models.Avg("rating"))["avg"]
    by_quality = list(
        tasks.exclude(quality_of_task="").values("quality_of_task").annotate(count=db_models.Count("id"))
    )
    
    return Response({
        "summary": {
            "total_tasks": total,
            "completed": completed,
            "completion_rate": round((completed / total) * 100, 1) if total else 0,
            "total_hours": float(total_hours),
            "avg_rating": round(avg_rating, 2) if avg_rating else None,
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