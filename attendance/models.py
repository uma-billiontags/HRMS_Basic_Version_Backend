# attendance/models.py
from django.db import models
from accounts.models import Employee, Admin


class Attendance(models.Model):
    class Status(models.TextChoices):
        EARLY = "Early", "Early"
        ON_TIME = "On Time", "On Time"
        GRACE = "Grace", "Grace"
        LATE = "Late", "Late"
        HALF_DAY = "Half Day", "Half Day"
        ABSENT = "Absent", "Absent"
        WEEK_OFF = "Week Off", "Week Off"
        GOVT_HOLIDAY = "Govt Holiday", "Govt Holiday"
        LEAVE = "Leave", "Leave"
        ON_DUTY = "On Duty", "On Duty"
        PERMISSION = "Permission", "Permission"

    date = models.DateField(db_index=True)
    employee = models.ForeignKey(
        Employee, on_delete=models.CASCADE, null=True, blank=True,
        related_name="attendance_records"
    )
    admin = models.ForeignKey(
        Admin, on_delete=models.CASCADE, null=True, blank=True,
        related_name="attendance_records_as_admin"
    )
    tag_id = models.CharField(max_length=50, blank=True, help_text="Employee Tag ID e.g. TAG095")
    in_time = models.TimeField(null=True, blank=True)
    out_time = models.TimeField(null=True, blank=True)
    total_hours = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True,
        help_text="Calculated total hours worked"
    )
    production_hours = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True,
        help_text="Total active production/task hours worked between in_time and out_time"
    )
    break_hours = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True,
        help_text="Total break, pause, and untracked hours between in_time and out_time"
    )
    status = models.CharField(
        max_length=30, choices=Status.choices, default=Status.ABSENT, db_index=True
    )
    remarks = models.TextField(blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-date", "employee__name"]
        constraints = [
            models.UniqueConstraint(
                fields=["employee", "date"],
                condition=models.Q(employee__isnull=False),
                name="unique_employee_date_attendance",
            ),
            models.UniqueConstraint(
                fields=["admin", "date"],
                condition=models.Q(admin__isnull=False),
                name="unique_admin_date_attendance",
            ),
        ]

    def __str__(self):
        actor_name = self.employee.name if self.employee else (self.admin.name if self.admin else "Unknown")
        return f"{self.date} | {actor_name} | {self.status} (In: {self.in_time}, Out: {self.out_time})"

    @property
    def actor_name(self):
        if self.employee:
            return self.employee.name
        if self.admin:
            return self.admin.name
        return "Unknown"

    @property
    def department_name(self):
        if self.employee:
            return self.employee.department or ""
        return "Admin"

    @property
    def designation_name(self):
        if self.employee:
            return self.employee.role or ""
        return "Admin"

    def save(self, *args, **kwargs):
        if not self.tag_id:
            if self.employee and self.employee.employee_id:
                self.tag_id = self.employee.employee_id
            elif self.employee:
                self.tag_id = f"TAG{self.employee.id:03d}"
            elif self.admin:
                self.tag_id = f"TAG{self.admin.id:03d}"
        super().save(*args, **kwargs)


class AttendanceComment(models.Model):
    attendance = models.ForeignKey(
        Attendance, on_delete=models.CASCADE, related_name="comments"
    )
    author_name = models.CharField(max_length=150)
    author_role = models.CharField(max_length=50)  # "employee" | "admin"
    comment = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.author_name}: {self.comment[:30]}"


class AttendanceCorrectionRequest(models.Model):
    class RequestType(models.TextChoices):
        IN_TIME = "in_time", "In Time"
        OUT_TIME = "out_time", "Out Time"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    attendance = models.ForeignKey(
        Attendance, on_delete=models.CASCADE, related_name="correction_requests",
        null=True, blank=True
    )
    employee = models.ForeignKey(
        Employee, on_delete=models.PROTECT, related_name="attendance_corrections",
        null=True, blank=True
    )
    admin = models.ForeignKey(
        Admin, on_delete=models.PROTECT, related_name="attendance_corrections_as_admin",
        null=True, blank=True
    )
    date = models.DateField(db_index=True)
    request_type = models.CharField(max_length=20, choices=RequestType.choices, default=RequestType.IN_TIME)
    system_time = models.TimeField(null=True, blank=True)
    requested_time = models.TimeField()
    reason = models.CharField(max_length=150)
    custom_reason = models.TextField(blank=True, default="")
    proof_screenshot = models.ImageField(upload_to="attendance_proofs/%Y/%m/", null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING, db_index=True)
    admin_notes = models.TextField(blank=True, default="")
    decided_by = models.ForeignKey(
        Admin, null=True, blank=True, on_delete=models.SET_NULL, related_name="decided_attendance_corrections"
    )
    decided_by_employee = models.ForeignKey(
        Employee, null=True, blank=True, on_delete=models.SET_NULL, related_name="decided_attendance_corrections_as_tl"
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        actor = self.employee.name if self.employee else (self.admin.name if self.admin else "Unknown")
        return f"{actor} - {self.date} {self.request_type} -> {self.requested_time} ({self.status})"
