# tasks/urls.py

from django.urls import path
from . import views

urlpatterns = [
    # ── Task CRUD & listing ──────────────────────────────────────────────
    path("get_all_tasks/", views.get_all_tasks, name="task-list"),
    path("create_task/", views.create_task, name="task-create"),
    path("create_and_assign/", views.create_and_assign_task, name="task-create-assign"),
    path("assign_task/<int:pk>/", views.assign_task, name="task-assign"),

    path("get_all_departments/", views.get_all_departments, name="task-departments"),
    path("get_all_employees/", views.get_all_employees, name="task-employees"),
    path("get_priority_choices/", views.get_priority_choices, name="task-priorities"),

    path("my_tasks/", views.get_my_tasks, name="employee-my-tasks"),
    path("tl_tasks/", views.get_tl_tasks, name="tl-tasks"),

    # ── Timer flow ────────────────────────────────────────────────────────
    path("my_active_session/", views.get_active_session, name="task-active-session"),
    path("<int:pk>/start/", views.start_task, name="task-start"),
    path("<int:pk>/pause/", views.pause_task, name="task-pause"),
    path("<int:pk>/resume/", views.resume_task, name="task-resume"),
    path("<int:pk>/submit/", views.submit_task, name="task-submit"),
    path("<int:pk>/sessions/", views.get_task_sessions, name="task-sessions"),

    # ── Review queue (admin + TL) ────────────────────────────────────────
    path("review_tasks/", views.get_review_tasks, name="task-review-tasks"),
    path("tl_review_tasks/", views.get_tl_review_tasks, name="tl-review-tasks"),
    path("<int:pk>/review/start/", views.start_review, name="task-review-start"),
    path("<int:pk>/review/approve/", views.approve_task, name="task-review-approve"),
    path("<int:pk>/review/rework/", views.request_rework, name="task-review-rework"),
    path("rework_tasks/", views.get_rework_tasks, name="task-rework-tasks"),

    # ── Time corrections ─── NOTE: these use "sessions/" and "corrections/",
    # NOT "tasks/" — make sure whatever include() maps this urls.py doesn't
    # prefix everything with /api/tasks/, or these paths won't match
    # /api/sessions/... and /api/corrections/... at all.
    path("sessions/<int:session_id>/correction-request/", views.request_correction, name="correction-request"),
    path("corrections/pending/", views.get_pending_corrections, name="correction-pending"),
    path("corrections/approved/", views.get_approved_corrections, name="correction-approved"),
    path("corrections/rejected/", views.get_rejected_corrections, name="correction-rejected"),
    path("corrections/<int:pk>/decision/", views.decide_correction, name="correction-decision"),

    # ── Hold / Cancel  ──────────────────────────────────────────
    path("<int:pk>/hold/", views.hold_task, name="task-hold"),
    path("<int:pk>/release_hold/", views.release_hold, name="task-release-hold"),
    path("<int:pk>/cancel/", views.cancel_task, name="task-cancel"),

    # ── Activity / audit log ─────────────────────────────────────────────
    path("activity/", views.get_all_activity, name="activity-all"),
    path("my_activity/", views.get_my_activity, name="activity-employee"),

    # ── Reports ───────────────────────────────────────────────────────────
    path("reports/admin/", views.get_admin_reports, name="reports-admin"),
    path("reports/employee/", views.get_my_reports, name="reports-employee"),

    # ── Task Master (reusable task catalog) ──────────────────────────────
    path("task_master/", views.get_task_master_list, name="task-master-list"),         # dropdown source, active-only
    path("task_master/get_all/", views.get_all_task_master, name="task-master-all"),   # management screen
    path("task_master/create/", views.create_task_master, name="task-master-create"),
    path("task_master/bulk_create/", views.bulk_create_task_master, name="task-master-bulk-create"),
    path("task_master/<int:pk>/edit/", views.edit_task_master, name="task-master-edit"),
    path("task_master/<int:pk>/delete/", views.delete_task_master, name="task-master-delete"),

    # ── Recurring (daily) tasks ──────────────────────────────────────────
    path("recurring/", views.get_recurring_tasks, name="recurring-list"),
    path("recurring/mine/", views.get_my_recurring_tasks, name="recurring-mine"),   # ← ADDED
    path("recurring/create/", views.create_recurring_task, name="recurring-create"),
    path("recurring/<int:pk>/stop/", views.stop_recurring_task, name="recurring-stop"),
    
    path("reports/rating_trends/", views.get_employee_rating_trends, name="reports-rating-trends"),
]