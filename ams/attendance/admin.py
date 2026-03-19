from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import AttendanceRecord, Department, Faculty, Student, Subject, TimetableEntry


@admin.register(Student)
class StudentAdmin(admin.ModelAdmin):
    list_display = ("name", "roll_number", "department", "section", "qr_mode", "user")
    search_fields = ("name", "roll_number", "user__username")
    list_filter = ("department", "qr_mode")


@admin.register(Faculty)
class FacultyAdmin(UserAdmin):
    list_display = ("username", "email", "first_name", "last_name", "is_active", "is_staff")
    list_filter = ("is_staff", "is_superuser", "is_active", "groups")
    fieldsets = (
        (None, {"fields": ("username", "password")} ),
        ("Personal info", {"fields": ("first_name", "last_name", "email")} ),
        ("Permissions", {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")} ),
        ("Important dates", {"fields": ("last_login", "date_joined")} ),
    )
    add_fieldsets = (
        (None, {
            "classes": ("wide",),
            "fields": ("username", "password1", "password2", "is_staff", "is_superuser"),
        }),
    )
    search_fields = ("username", "first_name", "last_name", "email")
    ordering = ("username",)


@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ("name",)
    search_fields = ("name",)


@admin.register(Subject)
class SubjectAdmin(admin.ModelAdmin):
    list_display = ("name", "department")
    list_filter = ("department",)
    search_fields = ("name",)


@admin.register(AttendanceRecord)
class AttendanceRecordAdmin(admin.ModelAdmin):
    list_display = ("student", "department", "subject", "date", "day_of_week", "time", "status", "method")
    list_filter = ("status", "method", "date", "student__department", "subject")
    search_fields = ("student__name", "student__roll_number")

    def department(self, obj):
        return obj.student.department if obj.student else None


@admin.register(TimetableEntry)
class TimetableEntryAdmin(admin.ModelAdmin):
    list_display = (
        "subject",
        "faculty",
        "program",
        "department",
        "semester",
        "section",
        "day_of_week",
        "start_time",
        "end_time",
        "is_active",
    )
    list_filter = ("day_of_week", "department", "semester", "is_active")
    search_fields = ("subject", "faculty__username", "program", "section", "semester")
