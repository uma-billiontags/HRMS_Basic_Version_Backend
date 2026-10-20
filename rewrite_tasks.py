# HRMS_Basic_Version_Backend/rewrite_tasks.py
import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "hrms_backend.settings")
django.setup()

from django.db import transaction
from tasks.models import Task, get_department_prefix

def run():
    print("Starting chronological task rewrite...")
    with transaction.atomic():
        # Order by assigned_date and primary key id for exact creation sequence
        all_tasks = list(Task.objects.select_related("assigned_to").order_by("assigned_date", "id"))
        print(f"Found {len(all_tasks)} tasks to rewrite.")

        # ── PASS 1: Assign temporary task_id to avoid UNIQUE constraint conflicts ──
        for task in all_tasks:
            task.task_id = f"TEMP-{task.pk}"
            task.save(update_fields=["task_id"])

        # ── PASS 2: Assign chronological TASK-X and department SUB_TASK_IDs ──
        dept_counters = {}

        for index, task in enumerate(all_tasks, start=1):
            # 1. Global Task ID: TASK-1, TASK-2, TASK-3...
            task.task_id = f"TASK-{index}"

            # 2. Get department
            dept = task.department
            if not dept and task.assigned_to and task.assigned_to.department:
                dept = task.assigned_to.department
                task.department = dept

            # 3. Department Sub ID: F-1, CD-1, etc.
            if dept:
                prefix = get_department_prefix(dept)
                dept_counters[prefix] = dept_counters.get(prefix, 0) + 1
                task.sub_task_id = f"{prefix}-{dept_counters[prefix]}"
            else:
                task.sub_task_id = None

            task.save(update_fields=["task_id", "sub_task_id", "department"])
            print(f"Updated Task #{task.id} -> {task.task_id} | {task.sub_task_id} ({dept})")

    print("\n✅ All existing tasks have been rewritten successfully!")

if __name__ == "__main__":
    run()