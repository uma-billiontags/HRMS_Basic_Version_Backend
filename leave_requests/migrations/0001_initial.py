import secrets
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True
    dependencies = [("accounts", "0005_admin_is_active")]
    operations = [
        migrations.CreateModel(
            name="EmployeeRequest",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("request_type", models.CharField(choices=[("leave", "Leave"), ("permission", "Permission"), ("custom", "Custom")], max_length=20)),
                ("recipient_email", models.EmailField(max_length=254)),
                ("subject", models.CharField(max_length=200)),
                ("body", models.TextField()),
                ("start_date", models.DateField(blank=True, null=True)),
                ("end_date", models.DateField(blank=True, null=True)),
                ("start_time", models.TimeField(blank=True, null=True)),
                ("end_time", models.TimeField(blank=True, null=True)),
                ("status", models.CharField(choices=[("pending", "Pending"), ("approved", "Approved"), ("rejected", "Rejected")], db_index=True, default="pending", max_length=20)),
                ("decision_note", models.TextField(blank=True, default="")),
                ("decision_token", models.CharField(default=secrets.token_urlsafe, editable=False, max_length=64, unique=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("decided_at", models.DateTimeField(blank=True, null=True)),
                ("employee", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="employee_requests", to="accounts.employee")),
            ],
            options={"ordering": ["-created_at"]},
        ),
    ]
