import re
import secrets
from django.db import models
from accounts.models import Employee, Admin


class EmployeeRequest(models.Model):
    class RequestType(models.TextChoices):
        LEAVE = "leave", "Leave"
        PERMISSION = "permission", "Permission"
        CUSTOM = "custom", "Custom"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="employee_requests")
    request_type = models.CharField(max_length=20, choices=RequestType.choices)
    recipient_email = models.TextField(help_text="Recipient email address(es), comma-separated")
    cc_email = models.TextField(blank=True, default="", help_text="CC email address(es), comma-separated")
    subject = models.CharField(max_length=200)
    body = models.TextField()
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    start_time = models.TimeField(null=True, blank=True)
    end_time = models.TimeField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING, db_index=True)
    decision_note = models.TextField(blank=True, default="")
    decision_token = models.CharField(max_length=64, default=secrets.token_urlsafe, unique=True, editable=False)
    decided_by_admin = models.ForeignKey(Admin, null=True, blank=True, on_delete=models.SET_NULL, related_name="decided_requests")
    decided_by_employee = models.ForeignKey(Employee, null=True, blank=True, on_delete=models.SET_NULL, related_name="decided_requests_as_tl")
    created_at = models.DateTimeField(auto_now_add=True)
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.get_request_type_display()} from {self.employee.name}: {self.subject}"

    def get_recipient_list(self):
        if not self.recipient_email:
            return []
        return [part.strip() for part in re.split(r"[,;\s]+", self.recipient_email) if part.strip()]

    def get_cc_list(self):
        if not self.cc_email:
            return []
        return [part.strip() for part in re.split(r"[,;\s]+", self.cc_email) if part.strip()]
