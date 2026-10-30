# attendance/serializers.py
from rest_framework import serializers
from .models import Attendance, AttendanceComment, AttendanceCorrectionRequest
import datetime


class AttendanceCommentSerializer(serializers.ModelSerializer):
    class Meta:
        model = AttendanceComment
        fields = ["id", "author_name", "author_role", "comment", "created_at"]


class AttendanceCorrectionSerializer(serializers.ModelSerializer):
    employee_name = serializers.SerializerMethodField()
    department = serializers.SerializerMethodField()
    decided_by_name = serializers.SerializerMethodField()
    requested_time_formatted = serializers.SerializerMethodField()
    system_time_formatted = serializers.SerializerMethodField()
    proof_screenshot_url = serializers.SerializerMethodField()

    class Meta:
        model = AttendanceCorrectionRequest
        fields = [
            "id", "attendance", "employee", "admin", "employee_name", "department",
            "date", "request_type", "system_time", "system_time_formatted",
            "requested_time", "requested_time_formatted", "reason", "custom_reason",
            "proof_screenshot", "proof_screenshot_url", "status", "admin_notes",
            "decided_by", "decided_by_name", "decided_at", "created_at", "updated_at",
        ]

    def get_employee_name(self, obj):
        if obj.employee:
            return obj.employee.name
        if obj.admin:
            return obj.admin.name
        return "Unknown"

    def get_department(self, obj):
        if obj.employee:
            return obj.employee.department or ""
        return "Admin"

    def get_decided_by_name(self, obj):
        if obj.decided_by:
            return f"Admin ({obj.decided_by.name})"
        if getattr(obj, "decided_by_employee", None):
            return f"TL ({obj.decided_by_employee.name})"
        return None

    def get_requested_time_formatted(self, obj):
        if obj.requested_time:
            return obj.requested_time.strftime("%H:%M")
        return None

    def get_system_time_formatted(self, obj):
        if obj.system_time:
            return obj.system_time.strftime("%H:%M:%S")
        return None

    def get_proof_screenshot_url(self, obj):
        if obj.proof_screenshot:
            request = self.context.get("request")
            if request:
                return request.build_absolute_uri(obj.proof_screenshot.url)
            return obj.proof_screenshot.url
        return None


from .calculator import calculate_attendance_hours


class AttendanceSerializer(serializers.ModelSerializer):
    day_name = serializers.SerializerMethodField()
    employee_name = serializers.CharField(source="actor_name", read_only=True)
    department = serializers.CharField(source="department_name", read_only=True)
    designation = serializers.CharField(source="designation_name", read_only=True)
    comments = AttendanceCommentSerializer(many=True, read_only=True)
    in_time_formatted = serializers.SerializerMethodField()
    out_time_formatted = serializers.SerializerMethodField()
    production_hours = serializers.SerializerMethodField()
    task_work_hours = serializers.SerializerMethodField()
    break_hours = serializers.SerializerMethodField()
    normal_break_hours = serializers.SerializerMethodField()
    other_pause_hours = serializers.SerializerMethodField()
    untracked_hours = serializers.SerializerMethodField()
    tag_id = serializers.SerializerMethodField()

    class Meta:
        model = Attendance
        fields = [
            "id", "date", "day_name", "employee", "admin", "employee_name",
            "tag_id", "designation", "department", "in_time", "out_time",
            "in_time_formatted", "out_time_formatted",
            "total_hours", "production_hours", "task_work_hours", "break_hours",
            "normal_break_hours", "other_pause_hours", "untracked_hours",
            "status", "remarks",
            "comments", "created_at", "updated_at",
        ]

    def _get_calc_data(self, obj):
        if not hasattr(obj, "_cached_hours_breakdown"):
            if obj.in_time:
                res = calculate_attendance_hours(obj)
                obj._cached_hours_breakdown = {
                    k: float(v) if v is not None else None
                    for k, v in res.items()
                }
            else:
                obj._cached_hours_breakdown = {}
        return obj._cached_hours_breakdown

    def get_production_hours(self, obj):
        val = self._get_calc_data(obj).get("production_hours")
        if val is not None:
            return val
        return float(obj.production_hours) if obj.production_hours is not None else None

    def get_task_work_hours(self, obj):
        return self._get_calc_data(obj).get("task_work_hours")

    def get_break_hours(self, obj):
        val = self._get_calc_data(obj).get("break_hours")
        if val is not None:
            return val
        return float(obj.break_hours) if obj.break_hours is not None else None

    def get_normal_break_hours(self, obj):
        return self._get_calc_data(obj).get("normal_break_hours")

    def get_other_pause_hours(self, obj):
        return self._get_calc_data(obj).get("other_pause_hours")

    def get_untracked_hours(self, obj):
        return self._get_calc_data(obj).get("untracked_hours")

    def get_tag_id(self, obj):
        if obj.tag_id:
            return obj.tag_id
        if obj.employee and obj.employee.employee_id:
            return obj.employee.employee_id
        if obj.employee:
            return f"TAG{obj.employee.id:03d}"
        if obj.admin:
            return f"TAG{obj.admin.id:03d}"
        return "" 

    def get_day_name(self, obj):
        if obj.date:
            return obj.date.strftime("%A")
        return ""

    def get_in_time_formatted(self, obj):
        if obj.in_time:
            return obj.in_time.strftime("%H:%M:%S")
        return None

    def get_out_time_formatted(self, obj):
        if obj.out_time:
            return obj.out_time.strftime("%H:%M:%S")
        return None
