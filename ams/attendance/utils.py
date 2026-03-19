from datetime import timedelta

from django.utils import timezone

from .models import AttendanceRecord, Subject, Student


def can_mark_attendance(student: Student, subject: Subject | None = None, window_minutes: int = 20) -> bool:
    """Return True if student can be marked present for the given subject.

    This applies the "20-minute rule": if the student already has an attendance
    record for the same subject within the last `window_minutes`, it returns False.
    """

    now = timezone.localtime(timezone.now())
    window_start = now - timedelta(minutes=window_minutes)

    queryset = AttendanceRecord.objects.filter(student=student, created_at__gte=window_start)
    if subject:
        queryset = queryset.filter(subject=subject)

    return not queryset.exists()


def get_last_attendance_time(student: Student, subject: Subject | None = None):
    """Return the datetime of the last attendance record (if any)."""
    qs = AttendanceRecord.objects.filter(student=student)
    if subject:
        qs = qs.filter(subject=subject)
    last = qs.order_by("-created_at").first()
    return last.created_at if last else None
