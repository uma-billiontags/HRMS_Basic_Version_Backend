# attendance/calculator.py
import datetime
from decimal import Decimal
from django.utils import timezone
from django.db import models


def merge_intervals(intervals):
    """
    Merge overlapping or contiguous datetime intervals: [(start, end), ...]
    """
    if not intervals:
        return []
    sorted_intervals = sorted(intervals, key=lambda x: x[0])
    merged = [sorted_intervals[0]]
    for current in sorted_intervals[1:]:
        prev_start, prev_end = merged[-1]
        if current[0] < prev_end:
            merged[-1] = (prev_start, max(prev_end, current[1]))
        else:
            merged.append(current)
    return merged


def calculate_attendance_hours(record, now_dt=None):
    """
    Computes total_hours (gross clocking), production_hours (active task work),
    and break_hours (normal breaks + other pauses + untracked idle time) strictly
    between record.in_time and record.out_time (or current time if active today).
    
    Any task timer running BEFORE check-in or AFTER check-out is strictly excluded.
    If no check-in exists, returns None for all hours.
    """
    from tasks.models import TimerSession, TaskPauseLog

    if not record or not record.in_time or not record.date:
        return {
            "total_hours": None,
            "production_hours": None,
            "task_work_hours": None,
            "untracked_hours": None,
            "break_hours": None,
            "normal_break_hours": None,
            "other_pause_hours": None,
        }

    tz = timezone.get_current_timezone()
    in_dt = timezone.make_aware(datetime.datetime.combine(record.date, record.in_time), tz)
    
    today = timezone.localdate()
    current_now = now_dt or timezone.now()

    if record.out_time:
        out_dt = timezone.make_aware(datetime.datetime.combine(record.date, record.out_time), tz)
        if out_dt < in_dt:
            out_dt += datetime.timedelta(days=1)
    elif record.date == today:
        out_dt = current_now
        if out_dt < in_dt:
            out_dt = in_dt
    else:
        # Past record without punch out
        return {
            "total_hours": record.total_hours,
            "production_hours": record.production_hours,
            "task_work_hours": None,
            "untracked_hours": None,
            "break_hours": record.break_hours,
            "normal_break_hours": None,
            "other_pause_hours": None,
        }

    gross_seconds = max(0.0, (out_dt - in_dt).total_seconds())
    gross_hours = round(Decimal(str(gross_seconds)) / Decimal("3600"), 2)

    actor_filter = {}
    if record.employee:
        actor_filter["employee"] = record.employee
    elif record.admin:
        actor_filter["admin"] = record.admin
    else:
        return {
            "total_hours": gross_hours,
            "production_hours": gross_hours,
            "task_work_hours": Decimal("0.00"),
            "untracked_hours": gross_hours,
            "break_hours": Decimal("0.00"),
            "normal_break_hours": Decimal("0.00"),
            "other_pause_hours": Decimal("0.00"),
        }

    # Fetch TimerSessions overlapping [in_dt, out_dt]
    sessions = TimerSession.objects.filter(
        start_time__lt=out_dt,
        **actor_filter
    ).filter(
        models.Q(end_time__isnull=True) | models.Q(end_time__gt=in_dt)
    ).order_by("start_time")

    work_intervals = []
    for s in sessions:
        s_start = s.start_time
        s_end = s.end_time if s.end_time else current_now
        c_start = max(s_start, in_dt)
        c_end = min(s_end, out_dt)
        if c_end > c_start:
            work_intervals.append((c_start, c_end))

    merged_work = merge_intervals(work_intervals)
    total_work_seconds = sum((end - start).total_seconds() for start, end in merged_work)
    total_work_seconds = min(gross_seconds, max(0.0, total_work_seconds))
    task_work_hours = round(Decimal(str(total_work_seconds)) / Decimal("3600"), 2)

    # Fetch TaskPauseLogs overlapping [in_dt, out_dt]
    pauses = TaskPauseLog.objects.filter(
        start_time__lt=out_dt,
        **actor_filter
    ).filter(
        models.Q(end_time__isnull=True) | models.Q(end_time__gt=in_dt)
    ).order_by("start_time")

    break_intervals = []
    other_pause_intervals = []
    for p in pauses:
        p_start = p.start_time
        p_end = p.end_time if p.end_time else current_now
        c_start = max(p_start, in_dt)
        c_end = min(p_end, out_dt)
        if c_end > c_start:
            if p.pause_type == TaskPauseLog.PauseType.BREAK:
                break_intervals.append((c_start, c_end))
            else:
                other_pause_intervals.append((c_start, c_end))

    merged_breaks = merge_intervals(break_intervals)
    merged_other = merge_intervals(other_pause_intervals)
    normal_break_seconds = sum((end - start).total_seconds() for start, end in merged_breaks)
    other_pause_seconds = sum((end - start).total_seconds() for start, end in merged_other)

    normal_break_hours = round(Decimal(str(normal_break_seconds)) / Decimal("3600"), 2)
    other_pause_hours = round(Decimal(str(other_pause_seconds)) / Decimal("3600"), 2)

    # Break hours are STRICTLY the recorded breaks & pauses
    break_hours = round(normal_break_hours + other_pause_hours, 2)

    # Production hours includes active task work + untracked time (e.g. waiting for review, in-between tasks)
    # production_hours = gross_hours - break_hours
    production_hours = max(Decimal("0.00"), round(gross_hours - break_hours, 2))
    untracked_hours = max(Decimal("0.00"), round(production_hours - task_work_hours, 2))

    return {
        "total_hours": gross_hours,
        "production_hours": production_hours,
        "task_work_hours": task_work_hours,
        "untracked_hours": untracked_hours,
        "break_hours": break_hours,
        "normal_break_hours": normal_break_hours,
        "other_pause_hours": other_pause_hours,
    }
