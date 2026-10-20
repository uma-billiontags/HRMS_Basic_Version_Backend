# attendance/urls.py
from django.urls import path
from . import views

urlpatterns = [
    # Employee punch & personal attendance
    path("today_status/", views.get_today_status, name="attendance-today-status"),
    path("check_in/", views.check_in, name="attendance-check-in"),
    path("check_out/", views.check_out, name="attendance-check-out"),
    path("my_attendance/", views.get_my_attendance, name="attendance-my-attendance"),
    path("add_comment/", views.add_comment, name="attendance-add-comment"),

    # Employee time correction requests
    path("correction-request/", views.submit_correction_request, name="attendance-submit-correction"),
    path("corrections/", views.list_attendance_corrections, name="attendance-list-corrections"),
    path("corrections/<int:pk>/decision/", views.decide_attendance_correction, name="attendance-decide-correction"),

    # Admin live & monthly views
    path("admin/daily/", views.admin_get_daily, name="attendance-admin-daily"),
    path("admin/monthly/", views.admin_get_monthly, name="attendance-admin-monthly"),
]
