# accounts/serializers.py

from rest_framework import serializers
from .models import Admin, Employee


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, style={"input_type": "password"})

    def validate(self, attrs):
        email = attrs["email"]
        password = attrs["password"]

        # Check Admin table first, then Employee table.
        admin = Admin.objects.filter(email=email).first()
        if admin and admin.check_password(password):
            attrs["principal"] = admin
            attrs["role"] = "admin"
            return attrs

        employee = Employee.objects.filter(email=email).first()
        if employee and employee.check_password(password):
            attrs["principal"] = employee
            attrs["role"] = "employee"
            return attrs

        raise serializers.ValidationError("Invalid email or password")


class AdminSerializer(serializers.ModelSerializer):
    class Meta:
        model = Admin
        fields = ["id", "name", "email"]


class EmployeeSerializer(serializers.ModelSerializer):
    class Meta:
        model = Employee
        fields = ["id", "name", "email", "department", "role"]
        

class EmployeeCredentialSerializer(serializers.ModelSerializer):
    """Used by the Team Access list/table — never exposes the password hash."""
    class Meta:
        model = Employee
        fields = ["id", "employee_id", "name", "email", "department", "role", "is_active", "created_at"]


class EmployeeWriteSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, required=False, allow_blank=True)

    class Meta:
        model = Employee
        fields = ["id", "employee_id", "name", "email", "password", "department", "role", "is_active"]
        extra_kwargs = {
            "name": {"required": True},
            "email": {"required": True},
            "is_active": {"required": False},
        }

    def validate_name(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Name cannot be blank.")
        return value

    def validate_employee_id(self, value):
        value = value.strip() if value else value
        if not value:
            raise serializers.ValidationError("Employee ID is required.")
        qs = Employee.objects.filter(employee_id__iexact=value)
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError("An employee with this ID already exists.")
        return value

    def validate_email(self, value):
        qs = Employee.objects.filter(email__iexact=value)
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError("An employee with this email already exists.")
        # ← NEW — cross-table check
        if Admin.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("This email is already registered as an Admin.")
        return value

    def validate(self, attrs):
        if self.instance is None and not attrs.get("password"):
            raise serializers.ValidationError({"password": "Password is required when creating a new employee."})
        return attrs
    
    def create(self, validated_data):
        raw_password = validated_data.pop("password")
        employee = Employee(**validated_data)
        employee.set_password(raw_password)
        employee.save()
        return employee

    def update(self, instance, validated_data):
        raw_password = validated_data.pop("password", None)
        for field, value in validated_data.items():
            setattr(instance, field, value)
        if raw_password:
            instance.set_password(raw_password)
        instance.save()
        return instance
    
# accounts/serializers.py — add these

class AdminCredentialSerializer(serializers.ModelSerializer):
    """Used by the Team Access Admin tab — never exposes the password hash."""
    class Meta:
        model = Admin
        fields = ["id", "name", "email", "is_active", "created_at"]


class AdminWriteSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, required=False, allow_blank=True)

    class Meta:
        model = Admin
        fields = ["id", "name", "email", "password", "is_active"]
        extra_kwargs = {
            "name": {"required": True},
            "email": {"required": True},
            "is_active": {"required": False},
        }

    def validate_name(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Name cannot be blank.")
        return value

    def validate_email(self, value):
        qs = Admin.objects.filter(email__iexact=value)
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError("An admin with this email already exists.")
        # ← NEW — cross-table check
        if Employee.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("This email is already registered as an Employee.")
        return value

    def validate(self, attrs):
        if self.instance is None and not attrs.get("password"):
            raise serializers.ValidationError({"password": "Password is required when creating a new admin."})
        return attrs

    def create(self, validated_data):
        raw_password = validated_data.pop("password")
        admin = Admin(**validated_data)
        admin.set_password(raw_password)
        admin.save()
        return admin

    def update(self, instance, validated_data):
        raw_password = validated_data.pop("password", None)
        for field, value in validated_data.items():
            setattr(instance, field, value)
        if raw_password:
            instance.set_password(raw_password)
        instance.save()
        return instance