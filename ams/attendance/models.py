import io

from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.core import signing
from django.db import models
from django.utils import timezone
from django.utils.crypto import get_random_string
import random
from datetime import timedelta


class Faculty(User):
    class Meta:
        proxy = True
        verbose_name = "Faculty"
        verbose_name_plural = "Faculties"


def _student_qr_upload_to(instance, filename):
    # Store QR codes under media/qr_codes/<roll_number>.png
    base = f"qr_codes/{instance.roll_number}.png"
    return base


class Department(models.Model):
    name = models.CharField(max_length=64, unique=True)

    def __str__(self):
        return self.name


class Subject(models.Model):
    name = models.CharField(max_length=128)
    department = models.ForeignKey(Department, on_delete=models.CASCADE, related_name="subjects")

    class Meta:
        unique_together = [("name", "department")]

    def __str__(self):
        return f"{self.name} ({self.department.name})"


class TimetableEntry(models.Model):
    DAY_CHOICES = [
        ("Monday", "Monday"),
        ("Tuesday", "Tuesday"),
        ("Wednesday", "Wednesday"),
        ("Thursday", "Thursday"),
        ("Friday", "Friday"),
        ("Saturday", "Saturday"),
        ("Sunday", "Sunday"),
    ]

    faculty = models.ForeignKey(User, on_delete=models.CASCADE, related_name="timetable_entries")
    program = models.CharField(max_length=64, blank=True, default="")
    department = models.ForeignKey(Department, on_delete=models.SET_NULL, null=True, blank=True, related_name="timetable_entries")
    semester = models.CharField(max_length=32, blank=True, default="")
    section = models.CharField(max_length=64)
    subject = models.CharField(max_length=128)
    day_of_week = models.CharField(max_length=16, choices=DAY_CHOICES)
    start_time = models.TimeField()
    end_time = models.TimeField()
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["day_of_week", "start_time", "section"]

    def __str__(self):
        return f"{self.subject} - {self.day_of_week} {self.start_time} ({self.section})"


class AttendanceSession(models.Model):
    subject = models.CharField(max_length=128)
    department_name = models.CharField(max_length=64, blank=True, default="")
    section = models.CharField(max_length=64)
    semester = models.CharField(max_length=32, blank=True, default="")
    attendance_date = models.DateField(null=True, blank=True)
    attendance_time = models.TimeField(null=True, blank=True)
    start_time = models.DateTimeField(auto_now_add=True)
    # New: end time limits when students can mark attendance
    end_time = models.DateTimeField(null=True, blank=True)
    # duration in minutes for the session (defaults to 10 minutes)
    duration_minutes = models.PositiveSmallIntegerField(default=10)
    is_active = models.BooleanField(default=True)
    session_code = models.CharField(max_length=12, unique=True, editable=False)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        ordering = ["-start_time"]
        constraints = [
            models.UniqueConstraint(fields=["section"], condition=models.Q(is_active=True), name="unique_active_session_per_section"),
        ]

    def save(self, *args, **kwargs):
        if self.is_active:
            AttendanceSession.objects.filter(section=self.section, is_active=True).exclude(pk=self.pk).update(is_active=False)

        if not self.attendance_date:
            self.attendance_date = timezone.localdate()

        if not self.attendance_time:
            self.attendance_time = timezone.localtime().time().replace(second=0, microsecond=0)

        # Ensure a human-friendly 4-digit numeric code for active sessions
        if not self.session_code:
            # Try to generate a unique 4-digit numeric code for active sessions
            tries = 0
            code = None
            while tries < 20:
                candidate = str(random.randint(1000, 9999))
                exists = AttendanceSession.objects.filter(session_code=candidate, is_active=True)
                if not exists.exists():
                    code = candidate
                    break
                tries += 1
            # Fallback to longer random string if collision or DB not available yet
            if code is None:
                code = get_random_string(12).upper()
            self.session_code = code

        # Ensure end_time exists and is derived from start_time + duration when missing
        if not self.end_time:
            base_start = getattr(self, "start_time") or timezone.localtime()
            try:
                self.end_time = base_start + timedelta(minutes=int(self.duration_minutes or 10))
            except Exception:
                self.end_time = base_start + timedelta(minutes=10)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.subject} ({self.section}) - {self.session_code}"


class Student(models.Model):
    QR_MODE_PERMANENT = "permanent"
    QR_MODE_DAILY = "daily"
    QR_MODE_CHOICES = [
        (QR_MODE_PERMANENT, "Permanent QR"),
        (QR_MODE_DAILY, "Daily QR"),
    ]

    user = models.OneToOneField(User, on_delete=models.SET_NULL, null=True, blank=True)
    name = models.CharField(max_length=128)
    roll_number = models.CharField(max_length=64, unique=True)
    department = models.ForeignKey(Department, on_delete=models.SET_NULL, null=True, blank=True)
    section = models.CharField(max_length=64, blank=True, null=True)
    qr_mode = models.CharField(max_length=16, choices=QR_MODE_CHOICES, default=QR_MODE_PERMANENT)
    face_image = models.ImageField(upload_to="face_images/", blank=True, null=True)
    face_encoding = models.JSONField(blank=True, null=True)
    qr_code = models.ImageField(upload_to=_student_qr_upload_to, blank=True, null=True)

    def __str__(self):
        return f"{self.name} ({self.roll_number})"

    @staticmethod
    def create_user_for_student(username, password):
        return User.objects.create_user(username=username, password=password)

    def permanent_qr_payload(self):
        return str(self.roll_number)

    def daily_qr_payload(self, date_value=None):
        active_date = date_value or timezone.localdate()
        return signing.dumps(
            {
                "roll_number": self.roll_number,
                "date": active_date.isoformat(),
                "mode": self.QR_MODE_DAILY,
            },
            salt="attendance.student.qr",
            compress=True,
        )

    def _build_qr_content(self, payload):
        try:
            import qrcode
        except ImportError:
            return None

        qr = qrcode.QRCode(box_size=10, border=2)
        qr.add_data(payload)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")

        bio = io.BytesIO()
        img.save(bio, format="PNG")
        bio.seek(0)

        return bio.read()

    def _generate_qr(self, payload=None):
        qr_bytes = self._build_qr_content(payload or self.permanent_qr_payload())
        if not qr_bytes:
            return

        self.qr_code.save(f"qr_{self.roll_number}.png", ContentFile(qr_bytes), save=False)

    def _generate_encoding(self):
        if not self.face_image:
            return
        try:
            import os

            # Force DeepFace to use legacy tf-keras for compatibility.
            os.environ.setdefault("TF_USE_LEGACY_KERAS", "1")
            from deepface import DeepFace
        except ImportError:
            return

        try:
            result = DeepFace.represent(
                img_path=self.face_image.path,
                model_name="Facenet",
                enforce_detection=False,
            )
            if result:
                self.face_encoding = result[0]["embedding"]
        except Exception:
            pass

    def save(self, *args, **kwargs):
        # Ensure face encoding exists when face_image is uploaded
        if self.face_image and not self.face_encoding:
            self._generate_encoding()

        # Save first to ensure we have a primary key for file paths
        super().save(*args, **kwargs)

        # Generate or refresh the permanent QR code when required.
        if self.qr_mode == self.QR_MODE_PERMANENT and not self.qr_code:
            self._generate_qr(self.permanent_qr_payload())
            super().save(update_fields=["qr_code"])


class AttendanceRecord(models.Model):
    STATUS_PRESENT = "present"
    STATUS_ABSENT = "absent"
    STATUS_LATE = "late"

    METHOD_QR = "qr"
    METHOD_FACE = "face"
    METHOD_MANUAL = "manual"
    METHOD_QR_FACE = "qr_face"

    STATUS_CHOICES = [
        (STATUS_PRESENT, "Present"),
        (STATUS_ABSENT, "Absent"),
        (STATUS_LATE, "Late"),
    ]

    METHOD_CHOICES = [
        (METHOD_QR, "QR Code"),
        (METHOD_FACE, "Facial Recognition"),
        (METHOD_MANUAL, "Manual"),
        (METHOD_QR_FACE, "QR + Face"),
    ]

    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name="attendance_records")
    session = models.ForeignKey(AttendanceSession, on_delete=models.SET_NULL, null=True, blank=True, related_name="attendance_records")
    subject = models.ForeignKey(Subject, on_delete=models.SET_NULL, null=True, blank=True)
    date = models.DateField()
    day_of_week = models.CharField(max_length=16, blank=True)
    time = models.TimeField()
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_PRESENT)
    method = models.CharField(max_length=16, choices=METHOD_CHOICES, default=METHOD_MANUAL)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        unique_together = [("student", "date", "subject", "time")]

    def __str__(self):
        subject_name = self.subject.name if self.subject else "General"
        return f"{self.student} — {self.date} {self.time} ({subject_name})"

    def save(self, *args, **kwargs):
        # Ensure date/time are populated
        now = timezone.localtime(timezone.now())
        if not self.date:
            self.date = now.date()
        if not self.time:
            self.time = now.time()
        if not self.day_of_week:
            self.day_of_week = now.strftime("%A")
        super().save(*args, **kwargs)
