import secrets
from django.db import models
from accounts.models import Employee


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
    recipient_email = models.EmailField()
    subject = models.CharField(max_length=200)
    body = models.TextField()
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    start_time = models.TimeField(null=True, blank=True)
    end_time = models.TimeField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING, db_index=True)
    decision_note = models.TextField(blank=True, default="")
    decision_token = models.CharField(max_length=64, default=secrets.token_urlsafe, unique=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.get_request_type_display()} from {self.employee.name}: {self.subject}"
