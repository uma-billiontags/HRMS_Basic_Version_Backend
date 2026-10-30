# attendance/views.py
import datetime
import calendar
from decimal import Decimal
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from accounts.models import Employee, Admin
from .models import Attendance, AttendanceComment, AttendanceCorrectionRequest
from .serializers import (
    AttendanceSerializer,
    AttendanceCommentSerializer,
    AttendanceCorrectionSerializer,
)
from .calculator import calculate_attendance_hours

OFFICE_START_TIME = datetime.time(9, 30, 0)
GRACE_CUTOFF_TIME = datetime.time(9, 45, 0)
HALF_DAY_CUTOFF_TIME = datetime.time(13, 30, 0)
HALF_DAY_MIN_HOURS = Decimal("4.50")


def _is_admin(request): 
    return getattr(request.user, "role", None) == "admin"


def _current_employee(request):
    if getattr(request.user, "role", None) == "employee":
        return request.user.instance
    return None


def _current_admin(request):
    if getattr(request.user, "role", None) == "admin":
        return request.user.instance
    return None


def _is_tl(request):
    emp = _current_employee(request)
    return emp is not None and getattr(emp, "role", "") in ("TL", "Team Lead")


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def get_today_status(request):
    """
    GET /api/attendance/today_status/
    Returns today's punch status for the logged-in user.
    """
    today = timezone.localdate()
    emp = _current_employee(request)
    adm = _current_admin(request)

    filter_kwargs = {"employee": emp} if emp else {"admin": adm}
    record = Attendance.objects.filter(date=today, **filter_kwargs).first()

    now_time = timezone.localtime().time()

    # Check if user submitted an in_time correction for today (pending or approved)
    today_corrections = AttendanceCorrectionRequest.objects.filter(
        date=today, **filter_kwargs
    )
    has_in_correction = today_corrections.filter(
        request_type=AttendanceCorrectionRequest.RequestType.IN_TIME
    ).exclude(status=AttendanceCorrectionRequest.Status.REJECTED).exists()

    has_out_correction = today_corrections.filter(
        request_type=AttendanceCorrectionRequest.RequestType.OUT_TIME
    ).exclude(status=AttendanceCorrectionRequest.Status.REJECTED).exists()

    has_checked_in = bool((record and record.in_time) or has_in_correction)
    has_checked_out = bool((record and record.out_time) or has_out_correction)

    return Response({
        "date": today.isoformat(),
        "current_server_time": now_time.strftime("%H:%M:%S"),
        "has_checked_in": has_checked_in,
        "has_checked_out": has_checked_out,
        "has_in_correction": has_in_correction,
        "has_out_correction": has_out_correction,
        "attendance": AttendanceSerializer(record, context={"request": request}).data if record else None,
        "is_sunday": today.weekday() == 6,
        "today_corrections": AttendanceCorrectionSerializer(
            today_corrections, many=True, context={"request": request}
        ).data,
    })


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def check_in(request):
    """
    POST /api/attendance/check_in/
    Locks server timestamp as in_time.
    Once checked in, it is LOCKED for the day (single punch check-in).
    """
    today = timezone.localdate()
    now_time = timezone.localtime().time()
    emp = _current_employee(request)
    adm = _current_admin(request)

    filter_kwargs = {"employee": emp} if emp else {"admin": adm}
    record, created = Attendance.objects.get_or_create(date=today, **filter_kwargs)

    # Check if already checked in
    if record.in_time is not None:
        return Response(
            {
                "detail": f"You have already checked in today at {record.in_time.strftime('%H:%M:%S')}. Check-in time is locked."
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Check if in_time correction has already been submitted for today
    has_in_correction = AttendanceCorrectionRequest.objects.filter(
        date=today,
        request_type=AttendanceCorrectionRequest.RequestType.IN_TIME,
        **filter_kwargs
    ).exclude(status=AttendanceCorrectionRequest.Status.REJECTED).exists()
    if has_in_correction:
        return Response(
            {
                "detail": "A check-in time correction request has already been submitted for today."
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Calculate status based on time lock rules
    if now_time < OFFICE_START_TIME:
        punch_status = Attendance.Status.EARLY
    elif now_time == OFFICE_START_TIME:
        punch_status = Attendance.Status.ON_TIME
    elif now_time <= GRACE_CUTOFF_TIME:
        punch_status = Attendance.Status.GRACE
    elif now_time >= HALF_DAY_CUTOFF_TIME:
        punch_status = Attendance.Status.HALF_DAY
    else:
        punch_status = Attendance.Status.LATE

    record.in_time = now_time
    record.status = punch_status
    if emp and emp.employee_id:
        record.tag_id = emp.employee_id
    elif not record.tag_id:
        record.tag_id = f"TAG{emp.id if emp else adm.id:03d}"

    remarks = request.data.get("remarks", "").strip()
    if remarks:
        record.remarks = remarks

    record.save()

    # If remark was provided, also create comment
    if remarks:
        author_name = emp.name if emp else adm.name
        author_role = "employee" if emp else "admin"
        AttendanceComment.objects.create(
            attendance=record,
            author_name=author_name,
            author_role=author_role,
            comment=remarks,
        )

    return Response(AttendanceSerializer(record).data, status=status.HTTP_200_OK)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def check_out(request):
    """
    POST /api/attendance/check_out/
    Locks server timestamp as out_time.
    Allows multiple checkouts throughout the day, updating to the latest checkout time.
    Calculates total hours worked and flags half-day if hours < 4.5.
    """
    today = timezone.localdate()
    now_time = timezone.localtime().time()
    emp = _current_employee(request)
    adm = _current_admin(request)

    filter_kwargs = {"employee": emp} if emp else {"admin": adm}
    record = Attendance.objects.filter(date=today, **filter_kwargs).first()

    if not record or record.in_time is None:
        return Response(
            {"detail": "You must check in before checking out."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Calculate duration
    in_dt = datetime.datetime.combine(today, record.in_time)
    out_dt = datetime.datetime.combine(today, now_time)
    if out_dt < in_dt:
        out_dt += datetime.timedelta(days=1)

    duration_secs = (out_dt - in_dt).total_seconds()
    total_hours = round(Decimal(str(duration_secs)) / Decimal("3600"), 2)

    record.out_time = now_time
    record.total_hours = total_hours

    # Calculate production and break hours strictly between in_time and out_time
    calc = calculate_attendance_hours(record)
    if calc["production_hours"] is not None:
        record.production_hours = calc["production_hours"]
    if calc["break_hours"] is not None:
        record.break_hours = calc["break_hours"]

    # If worked less than 4.5 hours, flag as Half Day unless already half day or special status
    if total_hours < HALF_DAY_MIN_HOURS and record.status not in (
        Attendance.Status.HALF_DAY,
        Attendance.Status.PERMISSION,
        Attendance.Status.ON_DUTY,
    ):
        record.status = Attendance.Status.HALF_DAY

    remarks = request.data.get("remarks", "").strip()
    if remarks:
        if record.remarks:
            record.remarks += f" | {remarks}"
        else:
            record.remarks = remarks

    record.save()

    if remarks:
        author_name = emp.name if emp else adm.name
        author_role = "employee" if emp else "admin"
        AttendanceComment.objects.create(
            attendance=record,
            author_name=author_name,
            author_role=author_role,
            comment=remarks,
        )

    return Response(AttendanceSerializer(record).data, status=status.HTTP_200_OK)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def get_my_attendance(request):
    """
    GET /api/attendance/my_attendance/?year=YYYY&month=MM
    Returns the monthly attendance calendar/table for logged-in user.
    Includes full calendar days of that month.
    """
    today = timezone.localdate()
    emp = _current_employee(request)
    adm = _current_admin(request)

    try:
        req_year = int(request.query_params.get("year", today.year))
    except (TypeError, ValueError):
        req_year = today.year

    try:
        req_month = int(request.query_params.get("month", today.month))
    except (TypeError, ValueError):
        req_month = today.month

    _, num_days = calendar.monthrange(req_year, req_month)
    month_start = datetime.date(req_year, req_month, 1)
    month_end = datetime.date(req_year, req_month, num_days)

    filter_kwargs = {"employee": emp} if emp else {"admin": adm}
    records = {
        r.date: r
        for r in Attendance.objects.filter(
            date__range=(month_start, month_end), **filter_kwargs
        ).prefetch_related("comments")
    }

    tag_id = (emp.employee_id if emp and emp.employee_id else (f"TAG{emp.id if emp else adm.id:03d}"))

    corrections_qs = AttendanceCorrectionRequest.objects.filter(
        date__range=(month_start, month_end), **filter_kwargs
    )
    corrections_map = {}
    for c in corrections_qs:
        corrections_map.setdefault(c.date, []).append(
            AttendanceCorrectionSerializer(c, context={"request": request}).data
        )

    days_list = []
    summary_counts = {
        "total_days": num_days,
        "present": 0,
        "absent": 0,
        "week_off": 0,
        "govt_holiday": 0,
        "early": 0,
        "on_time": 0,
        "grace": 0,
        "late": 0,
        "half_day": 0,
        "leave": 0,
        "total_hours": Decimal("0.00"),
        "production_hours": Decimal("0.00"),
        "break_hours": Decimal("0.00"),
    }

    for d in range(1, num_days + 1):
        dt = datetime.date(req_year, req_month, d)
        rec = records.get(dt)
        is_sunday = dt.weekday() == 6
        is_past_or_today = dt <= today

        if rec:
            data = AttendanceSerializer(rec, context={"request": request}).data
            if not data.get("tag_id"):
                data["tag_id"] = tag_id
            # Accumulate stats
            if rec.status == Attendance.Status.EARLY:
                summary_counts["early"] += 1
                summary_counts["present"] += 1
            elif rec.status == Attendance.Status.ON_TIME:
                summary_counts["on_time"] += 1
                summary_counts["present"] += 1
            elif rec.status == Attendance.Status.GRACE:
                summary_counts["grace"] += 1
                summary_counts["present"] += 1
            elif rec.status == Attendance.Status.LATE:
                summary_counts["late"] += 1
                summary_counts["present"] += 1
            elif rec.status == Attendance.Status.HALF_DAY:
                summary_counts["half_day"] += 1
                summary_counts["present"] += 1
            elif rec.status == Attendance.Status.WEEK_OFF:
                summary_counts["week_off"] += 1
            elif rec.status == Attendance.Status.GOVT_HOLIDAY:
                summary_counts["govt_holiday"] += 1
            elif rec.status == Attendance.Status.LEAVE:
                summary_counts["leave"] += 1
            elif rec.status == Attendance.Status.ABSENT:
                summary_counts["absent"] += 1
            else:
                summary_counts["present"] += 1

            if data.get("total_hours") is not None:
                summary_counts["total_hours"] += Decimal(str(data["total_hours"]))
            if data.get("production_hours") is not None:
                summary_counts["production_hours"] += Decimal(str(data["production_hours"]))
            if data.get("break_hours") is not None:
                summary_counts["break_hours"] += Decimal(str(data["break_hours"]))
        else:
            # Virtual day row for missing records
            if is_sunday:
                status_val = Attendance.Status.WEEK_OFF
                summary_counts["week_off"] += 1
            elif is_past_or_today:
                status_val = Attendance.Status.ABSENT
                summary_counts["absent"] += 1
            else:
                status_val = "Scheduled"

            data = {
                "id": None,
                "date": dt.isoformat(),
                "day_name": dt.strftime("%A"),
                "employee": emp.id if emp else None,
                "admin": adm.id if adm else None,
                "employee_name": emp.name if emp else adm.name,
                "tag_id": tag_id,
                "designation": emp.role if emp else "Admin",
                "department": emp.department if emp else "Admin",
                "in_time": None,
                "out_time": None,
                "in_time_formatted": None,
                "out_time_formatted": None,
                "total_hours": None,
                "production_hours": None,
                "task_work_hours": None,
                "break_hours": None,
                "normal_break_hours": None,
                "other_pause_hours": None,
                "untracked_hours": None,
                "status": status_val,
                "remarks": "",
                "comments": [],
            }

        data["corrections"] = corrections_map.get(dt, [])
        days_list.append(data)

    return Response({
        "year": req_year,
        "month": req_month,
        "month_name": month_start.strftime("%B %Y"),
        "employee_name": emp.name if emp else adm.name,
        "tag_id": tag_id,
        "summary": {
            **summary_counts,
            "total_hours": float(summary_counts["total_hours"]),
            "production_hours": float(summary_counts["production_hours"]),
            "break_hours": float(summary_counts["break_hours"]),
        },
        "days": days_list,
    })


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def add_comment(request):
    """
    POST /api/attendance/add_comment/
    body: { "attendance_id": int, "comment": str }
    Adds a comment to an attendance day record.
    """
    att_id = request.data.get("attendance_id")
    comment_text = request.data.get("comment", "").strip()

    if not att_id or not comment_text:
        return Response(
            {"detail": "attendance_id and comment are required."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    attendance = get_object_or_404(Attendance, pk=att_id)
    emp = _current_employee(request)
    adm = _current_admin(request)

    author_name = emp.name if emp else (adm.name if adm else "User")
    author_role = "employee" if emp else "admin"

    comment_obj = AttendanceComment.objects.create(
        attendance=attendance,
        author_name=author_name,
        author_role=author_role,
        comment=comment_text,
    )

    # Append to remarks if not already there
    if not attendance.remarks:
        attendance.remarks = comment_text
    else:
        attendance.remarks += f" | {comment_text}"
    attendance.save(update_fields=["remarks"])

    return Response(AttendanceCommentSerializer(comment_obj).data, status=status.HTTP_201_CREATED)


# ── ADMIN ENDPOINTS ─────────────────────────────────────────────────────────

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def admin_get_daily(request):
    """
    GET /api/attendance/admin/daily/?date=YYYY-MM-DD&department=
    Admin daily live view across all active employees.
    """
    if not _is_admin(request):
        return Response({"detail": "Admin only."}, status=status.HTTP_403_FORBIDDEN)

    date_str = request.query_params.get("date")
    if date_str:
        try:
            target_date = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            target_date = timezone.localdate()
    else:
        target_date = timezone.localdate()

    dept_filter = request.query_params.get("department", "").strip()
    employees = Employee.objects.filter(is_active=True).order_by("name")
    if dept_filter:
        employees = employees.filter(department__iexact=dept_filter)

    records = {
        r.employee_id: r
        for r in Attendance.objects.filter(date=target_date, employee__in=employees).prefetch_related("comments")
    }

    is_sunday = target_date.weekday() == 6
    is_past_or_today = target_date <= timezone.localdate()

    items = []
    stats = {
        "total_staff": employees.count(),
        "present": 0,
        "early": 0,
        "on_time": 0,
        "grace": 0,
        "late": 0,
        "half_day": 0,
        "absent": 0,
        "week_off": 0,
        "leave": 0,
        "not_checked_in": 0,
    }

    for emp in employees:
        rec = records.get(emp.id)
        if rec and rec.in_time:
            data = AttendanceSerializer(rec).data
            stats["present"] += 1
            if rec.status == Attendance.Status.EARLY:
                stats["early"] += 1
            elif rec.status == Attendance.Status.ON_TIME:
                stats["on_time"] += 1
            elif rec.status == Attendance.Status.GRACE:
                stats["grace"] += 1
            elif rec.status == Attendance.Status.LATE:
                stats["late"] += 1
            elif rec.status == Attendance.Status.HALF_DAY:
                stats["half_day"] += 1
        elif rec and rec.status in (Attendance.Status.LEAVE, Attendance.Status.WEEK_OFF, Attendance.Status.GOVT_HOLIDAY, Attendance.Status.ABSENT):
            data = AttendanceSerializer(rec).data
            if rec.status == Attendance.Status.LEAVE:
                stats["leave"] += 1
            elif rec.status == Attendance.Status.WEEK_OFF:
                stats["week_off"] += 1
            elif rec.status == Attendance.Status.ABSENT:
                stats["absent"] += 1
        else:
            # No record
            if is_sunday:
                status_val = Attendance.Status.WEEK_OFF
                stats["week_off"] += 1
            elif is_past_or_today:
                status_val = Attendance.Status.ABSENT if target_date < timezone.localdate() else "Not Checked In"
                if target_date < timezone.localdate():
                    stats["absent"] += 1
                else:
                    stats["not_checked_in"] += 1
            else:
                status_val = "Scheduled"

            data = {
                "id": None,
                "date": target_date.isoformat(),
                "day_name": target_date.strftime("%A"),
                "employee": emp.id,
                "employee_name": emp.name,
                "tag_id": emp.employee_id or f"TAG{emp.id:03d}",
                "designation": emp.role or "",
                "department": emp.department or "",
                "in_time": None,
                "out_time": None,
                "in_time_formatted": None,
                "out_time_formatted": None,
                "total_hours": None,
                "production_hours": None,
                "task_work_hours": None,
                "break_hours": None,
                "normal_break_hours": None,
                "other_pause_hours": None,
                "untracked_hours": None,
                "status": status_val,
                "remarks": "",
                "comments": [],
            }
        items.append(data)

    departments = (
        Employee.objects.filter(is_active=True)
        .exclude(department="")
        .values_list("department", flat=True)
        .distinct()
        .order_by("department")
    )

    return Response({
        "date": target_date.isoformat(),
        "formatted_date": target_date.strftime("%A, %d %B %Y"),
        "is_sunday": is_sunday,
        "stats": stats,
        "departments": list(departments),
        "records": items,
    })


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def admin_get_monthly(request):
    """
    GET /api/attendance/admin/monthly/?year=YYYY&month=MM&employee_id=&department=
    Full monthly attendance matrix/sheet matching the company Excel structure.
    """
    if not _is_admin(request):
        return Response({"detail": "Admin only."}, status=status.HTTP_403_FORBIDDEN)

    today = timezone.localdate()
    try:
        req_year = int(request.query_params.get("year", today.year))
    except (TypeError, ValueError):
        req_year = today.year

    try:
        req_month = int(request.query_params.get("month", today.month))
    except (TypeError, ValueError):
        req_month = today.month

    emp_id = request.query_params.get("employee_id")
    dept_filter = request.query_params.get("department", "").strip()

    _, num_days = calendar.monthrange(req_year, req_month)
    month_start = datetime.date(req_year, req_month, 1)
    month_end = datetime.date(req_year, req_month, num_days)

    employees = Employee.objects.filter(is_active=True).order_by("name")
    if emp_id and emp_id.isdigit():
        employees = employees.filter(pk=int(emp_id))
    elif dept_filter:
        employees = employees.filter(department__iexact=dept_filter)

    records = {
        (r.employee_id, r.date): r
        for r in Attendance.objects.filter(
            date__range=(month_start, month_end), employee__in=employees
        ).prefetch_related("comments")
    }

    # Build matrix: for each employee, full days of month
    employee_summaries = []
    matrix_rows = []

    for emp in employees:
        tag_id = emp.employee_id or f"TAG{emp.id:03d}"
        emp_stats = {
            "employee_id": emp.id,
            "employee_name": emp.name,
            "tag_id": tag_id,
            "department": emp.department or "",
            "designation": emp.role or "",
            "total_days": num_days,
            "present": 0,
            "absent": 0,
            "week_off": 0,
            "govt_holiday": 0,
            "early": 0,
            "on_time": 0,
            "grace": 0,
            "late": 0,
            "half_day": 0,
            "leave": 0,
            "total_hours": Decimal("0.00"),
            "production_hours": Decimal("0.00"),
            "break_hours": Decimal("0.00"),
        }

        for d in range(1, num_days + 1):
            dt = datetime.date(req_year, req_month, d)
            rec = records.get((emp.id, dt))
            is_sunday = dt.weekday() == 6
            is_past_or_today = dt <= today

            if rec:
                row_data = AttendanceSerializer(rec).data
                if not row_data.get("tag_id"):
                    row_data["tag_id"] = tag_id
                # Tally
                if rec.status == Attendance.Status.EARLY:
                    emp_stats["early"] += 1
                    emp_stats["present"] += 1
                elif rec.status == Attendance.Status.ON_TIME:
                    emp_stats["on_time"] += 1
                    emp_stats["present"] += 1
                elif rec.status == Attendance.Status.GRACE:
                    emp_stats["grace"] += 1
                    emp_stats["present"] += 1
                elif rec.status == Attendance.Status.LATE:
                    emp_stats["late"] += 1
                    emp_stats["present"] += 1
                elif rec.status == Attendance.Status.HALF_DAY:
                    emp_stats["half_day"] += 1
                    emp_stats["present"] += 1
                elif rec.status == Attendance.Status.WEEK_OFF:
                    emp_stats["week_off"] += 1
                elif rec.status == Attendance.Status.GOVT_HOLIDAY:
                    emp_stats["govt_holiday"] += 1
                elif rec.status == Attendance.Status.LEAVE:
                    emp_stats["leave"] += 1
                elif rec.status == Attendance.Status.ABSENT:
                    emp_stats["absent"] += 1
                else:
                    emp_stats["present"] += 1

                if row_data.get("total_hours") is not None:
                    emp_stats["total_hours"] += Decimal(str(row_data["total_hours"]))
                if row_data.get("production_hours") is not None:
                    emp_stats["production_hours"] += Decimal(str(row_data["production_hours"]))
                if row_data.get("break_hours") is not None:
                    emp_stats["break_hours"] += Decimal(str(row_data["break_hours"]))
            else:
                if is_sunday:
                    status_val = Attendance.Status.WEEK_OFF
                    emp_stats["week_off"] += 1
                elif is_past_or_today:
                    status_val = Attendance.Status.ABSENT
                    emp_stats["absent"] += 1
                else:
                    status_val = "Scheduled"

                row_data = {
                    "id": None,
                    "date": dt.isoformat(),
                    "day_name": dt.strftime("%A"),
                    "employee": emp.id,
                    "employee_name": emp.name,
                    "tag_id": tag_id,
                    "designation": emp.role or "",
                    "department": emp.department or "",
                    "in_time": None,
                    "out_time": None,
                    "in_time_formatted": None,
                    "out_time_formatted": None,
                    "total_hours": None,
                    "production_hours": None,
                    "task_work_hours": None,
                    "break_hours": None,
                    "normal_break_hours": None,
                    "other_pause_hours": None,
                    "untracked_hours": None,
                    "status": status_val,
                    "remarks": "",
                    "comments": [],
                }
            matrix_rows.append(row_data)

        emp_stats["total_hours"] = float(emp_stats["total_hours"])
        emp_stats["production_hours"] = float(emp_stats["production_hours"])
        emp_stats["break_hours"] = float(emp_stats["break_hours"])
        employee_summaries.append(emp_stats)

    departments = (
        Employee.objects.filter(is_active=True)
        .exclude(department="")
        .values_list("department", flat=True)
        .distinct()
        .order_by("department")
    )

    all_employees = list(Employee.objects.filter(is_active=True).values("id", "name", "department", "employee_id").order_by("name"))

    return Response({
        "year": req_year,
        "month": req_month,
        "month_name": month_start.strftime("%B %Y"),
        "total_days_in_month": num_days,
        "departments": list(departments),
        "employees": all_employees,
        "employee_summaries": employee_summaries,
        "matrix_rows": matrix_rows,
    })


# ── ATTENDANCE CORRECTION REQUEST ENDPOINTS ──────────────────────────────────

@api_view(["POST"])
@permission_classes([IsAuthenticated])
def submit_correction_request(request):
    """
    POST /api/attendance/correction-request/
    Body (multipart/form-data or JSON):
    date: YYYY-MM-DD
    request_type: 'in_time' | 'out_time'
    requested_time: 'HH:MM' or 'HH:MM:SS'
    reason: str
    custom_reason: str (optional)
    proof_screenshot: file (optional TimeChamp screenshot)
    """
    emp = _current_employee(request)
    adm = _current_admin(request)
    filter_kwargs = {"employee": emp} if emp else {"admin": adm}

    date_str = request.data.get("date")
    request_type = request.data.get("request_type", AttendanceCorrectionRequest.RequestType.IN_TIME)
    requested_time_str = request.data.get("requested_time")
    reason = request.data.get("reason", "").strip()
    custom_reason = request.data.get("custom_reason", "").strip()
    proof_file = request.FILES.get("proof_screenshot")

    if not date_str or not requested_time_str:
        return Response({"detail": "Date and requested time are required."}, status=status.HTTP_400_BAD_REQUEST)

    if not reason:
        return Response({"detail": "Please select or provide a reason."}, status=status.HTTP_400_BAD_REQUEST)

    try:
        req_date = datetime.date.fromisoformat(date_str)
    except ValueError:
        return Response({"detail": "Invalid date format. Use YYYY-MM-DD."}, status=status.HTTP_400_BAD_REQUEST)

    if req_date > timezone.localdate():
        return Response({"detail": "Cannot request attendance time correction for future dates."}, status=status.HTTP_400_BAD_REQUEST)

    try:
        parts = [int(p) for p in requested_time_str.split(":")[:2]]
        req_time = datetime.time(parts[0], parts[1], 0)
    except Exception:
        return Response({"detail": "Invalid requested time format. Use HH:MM."}, status=status.HTTP_400_BAD_REQUEST)

    record, _ = Attendance.objects.get_or_create(date=req_date, **filter_kwargs)

    # Determine system time snapshot
    system_time = record.in_time if request_type == AttendanceCorrectionRequest.RequestType.IN_TIME else record.out_time
    if not system_time and req_date == timezone.localdate():
        system_time = timezone.localtime().time()

    # Check if a pending correction request already exists for this date and type
    existing_pending = AttendanceCorrectionRequest.objects.filter(
        date=req_date,
        request_type=request_type,
        status=AttendanceCorrectionRequest.Status.PENDING,
        **filter_kwargs,
    ).first()
    if existing_pending:
        return Response(
            {
                "detail": f"A correction request for this {request_type.replace('_', ' ')} is already pending admin review."
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    correction = AttendanceCorrectionRequest.objects.create(
        attendance=record,
        employee=emp,
        admin=adm,
        date=req_date,
        request_type=request_type,
        system_time=system_time,
        requested_time=req_time,
        reason=reason,
        custom_reason=custom_reason,
        proof_screenshot=proof_file,
        status=AttendanceCorrectionRequest.Status.PENDING,
    )

    return Response(
        AttendanceCorrectionSerializer(correction, context={"request": request}).data,
        status=status.HTTP_201_CREATED,
    )


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def decide_attendance_correction(request, pk):
    """
    POST /api/attendance/corrections/<pk>/decision/
    Body: { "decision": "approve" | "reject", "admin_notes": "..." }
    """
    is_admin = _is_admin(request)
    is_tl = _is_tl(request)
    if not (is_admin or is_tl):
        return Response({"detail": "Admin or Team Lead access only."}, status=status.HTTP_403_FORBIDDEN)

    correction = get_object_or_404(AttendanceCorrectionRequest, pk=pk)
    if correction.status != AttendanceCorrectionRequest.Status.PENDING:
        return Response({"detail": "This request has already been decided."}, status=status.HTTP_400_BAD_REQUEST)

    emp_user = _current_employee(request)
    admin_user = _current_admin(request)

    # Security check: TL cannot decide on their own request or another TL's request
    if is_tl and not is_admin:
        if correction.employee_id == emp_user.id:
            return Response({"detail": "You cannot decide on your own correction request. It must be approved by an Administrator."}, status=status.HTTP_403_FORBIDDEN)
        if correction.employee and correction.employee.role in ("TL", "Team Lead"):
            return Response({"detail": "Team Lead correction requests must be decided by an Administrator."}, status=status.HTTP_403_FORBIDDEN)
        if correction.employee and correction.employee.belong_to_tl_id != emp_user.id:
            return Response({"detail": "You can only decide correction requests for your team members."}, status=status.HTTP_403_FORBIDDEN)

    decision = request.data.get("decision")
    admin_notes = request.data.get("admin_notes", "").strip()

    if decision not in ("approve", "reject"):
        return Response({"detail": "Decision must be 'approve' or 'reject'."}, status=status.HTTP_400_BAD_REQUEST)

    now = timezone.now()

    if decision == "approve":
        correction.status = AttendanceCorrectionRequest.Status.APPROVED
        correction.admin_notes = admin_notes
        if admin_user:
            correction.decided_by = admin_user
            correction.decided_by_employee = None
        else:
            correction.decided_by = None
            correction.decided_by_employee = emp_user
        correction.decided_at = now
        correction.save()

        # Update the Attendance record in DB!
        record = correction.attendance
        if not record:
            filter_kwargs = {"employee": correction.employee} if correction.employee else {"admin": correction.admin}
            record, _ = Attendance.objects.get_or_create(date=correction.date, **filter_kwargs)
            correction.attendance = record
            correction.save(update_fields=["attendance"])

        if correction.request_type == AttendanceCorrectionRequest.RequestType.IN_TIME:
            record.in_time = correction.requested_time
            # Recompute punch status based on new in_time
            if record.in_time < OFFICE_START_TIME:
                record.status = Attendance.Status.EARLY
            elif record.in_time == OFFICE_START_TIME:
                record.status = Attendance.Status.ON_TIME
            elif record.in_time <= GRACE_CUTOFF_TIME:
                record.status = Attendance.Status.GRACE
            elif record.in_time >= HALF_DAY_CUTOFF_TIME:
                record.status = Attendance.Status.HALF_DAY
            else:
                record.status = Attendance.Status.LATE
        else:
            record.out_time = correction.requested_time

        # Recalculate total hours if both times exist
        if record.in_time and record.out_time:
            in_dt = datetime.datetime.combine(record.date, record.in_time)
            out_dt = datetime.datetime.combine(record.date, record.out_time)
            if out_dt < in_dt:
                out_dt += datetime.timedelta(days=1)
            duration_secs = (out_dt - in_dt).total_seconds()
            total_hours = round(Decimal(str(duration_secs)) / Decimal("3600"), 2)
            record.total_hours = total_hours

            calc = calculate_attendance_hours(record)
            if calc["production_hours"] is not None:
                record.production_hours = calc["production_hours"]
            if calc["break_hours"] is not None:
                record.break_hours = calc["break_hours"]

            if total_hours < HALF_DAY_MIN_HOURS and record.status not in (
                Attendance.Status.HALF_DAY,
                Attendance.Status.PERMISSION,
                Attendance.Status.ON_DUTY,
            ):
                record.status = Attendance.Status.HALF_DAY

        if not record.tag_id:
            if record.employee and record.employee.employee_id:
                record.tag_id = record.employee.employee_id
            elif record.employee:
                record.tag_id = f"TAG{record.employee.id:03d}"

        correction_note = f"Time correction approved: {correction.request_type} updated to {correction.requested_time.strftime('%H:%M')}"
        if record.remarks:
            record.remarks += f" | {correction_note}"
        else:
            record.remarks = correction_note

        record.save()
    else:
        # Rejected: DO NOT update time in DB! Keep original times, but record rejection reason in Remarks & Notes!
        correction.status = AttendanceCorrectionRequest.Status.REJECTED
        correction.admin_notes = admin_notes
        if admin_user:
            correction.decided_by = admin_user
            correction.decided_by_employee = None
        else:
            correction.decided_by = None
            correction.decided_by_employee = emp_user
        correction.decided_at = now
        correction.save()

        record = correction.attendance
        if not record:
            filter_kwargs = {"employee": correction.employee} if correction.employee else {"admin": correction.admin}
            record, _ = Attendance.objects.get_or_create(
                date=correction.date,
                defaults={
                    "status": Attendance.Status.WEEK_OFF if correction.date.weekday() == 6 else Attendance.Status.ABSENT,
                    "tag_id": (
                        correction.employee.employee_id
                        if (correction.employee and correction.employee.employee_id)
                        else f"TAG{correction.employee.id if correction.employee else correction.admin.id:03d}"
                    ),
                },
                **filter_kwargs,
            )
            correction.attendance = record
            correction.save(update_fields=["attendance"])

        req_type_str = "In-Time" if correction.request_type == AttendanceCorrectionRequest.RequestType.IN_TIME else "Out-Time"
        rejection_reason = admin_notes if admin_notes else "Proof rejected by Admin"
        rejection_note = f"Rejected ({req_type_str}): {rejection_reason}"

        if record.remarks:
            record.remarks += f" | {rejection_note}"
        else:
            record.remarks = rejection_note

        if not record.tag_id:
            if record.employee and record.employee.employee_id:
                record.tag_id = record.employee.employee_id
            elif record.employee:
                record.tag_id = f"TAG{record.employee.id if record.employee else record.admin.id:03d}"

        record.save()

        AttendanceComment.objects.create(
            attendance=record,
            author_name=admin_user.name if admin_user else "Admin",
            author_role="admin",
            comment=f"Time correction request rejected: {rejection_reason}",
        )

    return Response(AttendanceCorrectionSerializer(correction, context={"request": request}).data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def list_attendance_corrections(request):
    """
    GET /api/attendance/corrections/?status=pending|approved|rejected&date=...&view=team|mine
    Admin gets all requests.
    TL with ?view=team gets all employee requests EXCLUDING TL's own requests and any other TL requests.
    Employee gets their own requests.
    """
    qs = AttendanceCorrectionRequest.objects.all().select_related("employee", "admin", "decided_by", "decided_by_employee", "attendance")
    view_type = request.query_params.get("view")

    if _is_admin(request):
        pass
    elif _is_tl(request) and view_type == "team":
        emp = _current_employee(request)
        # Show requests ONLY from employees who belong to THIS TL
        qs = qs.filter(employee__belong_to_tl=emp)
    else:
        emp = _current_employee(request)
        adm = _current_admin(request)
        if emp:
            qs = qs.filter(employee=emp)
        elif adm:
            qs = qs.filter(admin=adm)
        else:
            return Response([])

    status_filter = request.query_params.get("status")
    if status_filter:
        qs = qs.filter(status=status_filter)

    date_filter = request.query_params.get("date")
    if date_filter:
        qs = qs.filter(date=date_filter)

    return Response(
        AttendanceCorrectionSerializer(qs.order_by("-created_at"), many=True, context={"request": request}).data
    )


