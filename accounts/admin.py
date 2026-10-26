# accounts/admin.py
#
# Plain admin registration for both tables — one screen each, all fields
# together, password auto-hashed on save so it's never stored in plain text.

from django import forms
from django.contrib import admin
from .models import Admin, Employee


class HashedPasswordFormMixin(forms.ModelForm):
    """
    Shows a normal password box in the Django admin form, but hashes
    whatever is typed before saving. If left blank while editing an
    existing row, the old password is kept unchanged.
    """
    password = forms.CharField(
        widget=forms.PasswordInput(render_value=False),
        required=False,
        help_text="Leave blank to keep the current password when editing.",
    )

    def save(self, commit=True):
        instance = super().save(commit=False)
        raw_password = self.cleaned_data.get("password")
        if raw_password:
            instance.set_password(raw_password)
        elif not instance.pk:
            raise forms.ValidationError("Password is required when creating a new account.")
        if commit:
            instance.save()
        return instance


class AdminForm(HashedPasswordFormMixin):
    class Meta:
        model = Admin
        fields = ["name", "email", "password"]


class EmployeeForm(HashedPasswordFormMixin):
    class Meta:
        model = Employee
        fields = ["name", "email", "password", "department", "role", "belong_to_tl", "is_active"]


@admin.register(Admin)
class AdminAdmin(admin.ModelAdmin):
    form = AdminForm
    list_display = ("name", "email", "created_at")
    search_fields = ("name", "email")


@admin.register(Employee)
class EmployeeAdmin(admin.ModelAdmin):
    form = EmployeeForm
    list_display = ("name", "email", "department", "role", "created_at")
    list_filter = ("department", "role")
    search_fields = ("name", "email")