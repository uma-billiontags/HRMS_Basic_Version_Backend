"""
URL configuration for hrms_backend project.
"""
from django.contrib import admin
from django.urls import path, include, re_path
from django.conf import settings
from django.views.static import serve

urlpatterns = [
    path('admin/', admin.site.urls),
    path("api/auth/", include("accounts.urls")),
    path("api/tasks/", include("tasks.urls")),
    path("api/attendance/", include("attendance.urls")),
    path("api/requests/", include("leave_requests.urls")),
]

# Serve media files in BOTH development and production
urlpatterns += [
    re_path(r'^media/(?P<path>.*)$', serve, {'document_root': settings.MEDIA_ROOT}),
]
