from django.urls import path
from . import views

urlpatterns = [
    path("mine/", views.my_requests, name="employee-requests-mine"),
    path("create/", views.create_request, name="employee-request-create"),
    path("admin/", views.admin_requests, name="admin-requests-list"),
    path("admin/<int:pk>/decision/", views.admin_decide_request, name="admin-request-decision"),
    path("email-decision/<str:token>/<str:decision>/", views.email_decision, name="email-request-decision"),
]
