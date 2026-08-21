# tasks/admin.py

from django.contrib import admin
from .models import Task


@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    list_display = (
        "task_id", "task_name", "assigned_to",
        "priority", "task_status", "total_time_taken", "due_date",
    )
    list_filter = ("task_status", "priority")
    search_fields = ("task_id", "task_name", "assigned_to__name")
    readonly_fields = ("task_id", "assigned_date", "total_time_taken", "last_activity")
    
from .models import Task, TaskMaster

@admin.register(TaskMaster)
class TaskMasterAdmin(admin.ModelAdmin):
    list_display = ("project_name", "task_name", "default_hours", "is_active", "created_at")
    list_filter = ("is_active", "project_name")
    search_fields = ("project_name", "task_name")