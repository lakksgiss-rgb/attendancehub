import base64
import csv
import json
import re
import time

from datetime import datetime, timedelta
from io import StringIO
from urllib.parse import urlsplit

from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import User
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.core.files.base import ContentFile
from django.db import models
from django.http import HttpResponse, JsonResponse
from django.middleware.csrf import get_token
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.crypto import get_random_string
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_POST

from .forms import (
    AttendanceSessionForm,
    FacultyRegistrationForm,
    StudentEditForm,
    StudentRegistrationForm,
)
from .models import AttendanceRecord, AttendanceSession, Department, Student, Subject, TimetableEntry
from .utils import can_mark_attendance, get_last_attendance_time


QR_CREDENTIAL_SALT = "attendance.student.qr"
API_AUTH_TOKEN_SALT = "attendance.api.auth"
DAY_SEQUENCE = {
    "Monday": 0,
    "Tuesday": 1,
    "Wednesday": 2,
    "Thursday": 3,
    "Friday": 4,
    "Saturday": 5,
    "Sunday": 6,
}


def _ui_shell_context():
    today = timezone.localdate()
    return {
        "stats": {
            "active_sessions": AttendanceSession.objects.filter(is_active=True).count(),
            "total_students": Student.objects.count(),
            "total_faculty": User.objects.filter(is_staff=True, is_superuser=False).count(),
            "attendance_today": AttendanceRecord.objects.filter(date=today).count(),
            "departments": Department.objects.count(),
        },
    }


def _ui_login_context(role="student", error=None):
    is_admin = role == "admin"
    is_faculty = role == "professor"
    return {
        "role": role,
        "is_admin": is_admin,
        "is_faculty": is_faculty,
        "title": "Admin Control Login" if is_admin else "Faculty Command Login" if is_faculty else "Student Check-In Login",
        "subtitle": (
            "Access the admin operations dashboard to manage students, timetable data, and system-wide attendance control."
            if is_admin
            else "Open live sessions, monitor attendance, and manage active classes from one control surface."
            if is_faculty
            else "Enter your account to open session links, verify attendance, and view your records."
        ),
        "error_message": error,
    }


def _ui_admin_context(form_values=None):
    overview = _admin_overview_payload()
    form_values = form_values or {}
    entries = list(
        TimetableEntry.objects.select_related("faculty", "department")
        .order_by("faculty__username", "day_of_week", "start_time")
    )
    grouped_entries = []
    grouped_map = {}
    for entry in entries:
        faculty_name = entry.faculty.username
        if faculty_name not in grouped_map:
            grouped_map[faculty_name] = {
                "faculty_name": faculty_name,
                "entries": [],
                "entry_count": 0,
            }
            grouped_entries.append(grouped_map[faculty_name])
        grouped_map[faculty_name]["entries"].append(_serialize_timetable_entry(entry))
        grouped_map[faculty_name]["entry_count"] += 1

    students = [
        _serialize_student(student)
        for student in Student.objects.select_related("department", "user").order_by("name", "roll_number")[:10]
    ]
    faculty_members = [
        _serialize_faculty(member)
        for member in User.objects.filter(is_staff=True, is_superuser=False).order_by("username")[:10]
    ]
    timetable_options = _timetable_options_payload()
    student_options = _student_options_payload()

    return {
        **_ui_shell_context(),
        "overview": overview,
        "grouped_timetable": grouped_entries,
        "faculty_options": timetable_options["faculty"],
        "department_options": timetable_options["departments"],
        "qr_mode_options": student_options["qr_modes"],
        "day_options": list(DAY_SEQUENCE.keys()),
        "timetable_form": {
            "faculty_id": form_values.get("faculty_id", ""),
            "program": form_values.get("program", ""),
            "department_id": form_values.get("department_id", ""),
            "semester": form_values.get("semester", ""),
            "section": form_values.get("section", ""),
            "subject": form_values.get("subject", ""),
            "day_of_week": form_values.get("day_of_week", "Monday"),
            "start_time": form_values.get("start_time", "09:00"),
            "end_time": form_values.get("end_time", "10:00"),
        },
        "student_form": {
            "name": form_values.get("name", ""),
            "roll_number": form_values.get("roll_number", ""),
            "username": form_values.get("username", ""),
            "department_id": form_values.get("department_id", ""),
            "section": form_values.get("section", ""),
            "qr_mode": form_values.get("qr_mode", Student.QR_MODE_PERMANENT),
        },
        "faculty_form": {
            "username": form_values.get("username", ""),
            "first_name": form_values.get("first_name", ""),
            "last_name": form_values.get("last_name", ""),
            "email": form_values.get("email", ""),
            "is_active": bool(form_values.get("is_active", True)),
        },
        "students_preview": students,
        "students_total": Student.objects.count(),
        "faculty_preview": faculty_members,
        "faculty_total": User.objects.filter(is_staff=True, is_superuser=False).count(),
        "recent_attendance_preview": overview["recent_attendance"][:4],
        "recent_attendance_hidden": max(len(overview["recent_attendance"]) - 4, 0),
        "closed_sessions_preview": overview["recent_closed_sessions"][:4],
        "closed_sessions_hidden": max(len(overview["recent_closed_sessions"]) - 4, 0),
        "admin_preview_mode": True,
    }


def _ui_faculty_form_defaults(user, form_values=None, preset=None):
    defaults = {
        "department_name": "",
        "section": "",
        "semester": "",
        "subject": "",
        "attendance_date": "",
        "attendance_time": "",
    }
    if preset:
        defaults.update({
            "department_name": preset.get("department_name", ""),
            "section": preset.get("section", ""),
            "semester": preset.get("semester", ""),
            "subject": preset.get("subject", ""),
            "attendance_date": preset.get("attendance_date", ""),
            "attendance_time": preset.get("attendance_time", ""),
        })

    if form_values:
        for key in defaults:
            if key in form_values:
                defaults[key] = form_values.get(key, defaults[key])

    return defaults


def _ui_faculty_context(request, user, form_values=None, preset_key=None):
    overview = _faculty_overview_payload(request, user)
    sessions = [_session_payload(request, session) for session in AttendanceSession.objects.filter(is_active=True, created_by=user).order_by("-start_time")]
    current_class = overview.get("current_class")
    next_class = overview.get("next_class")
    preset = None
    if preset_key == "current" and current_class:
        preset = current_class.get("session_defaults")
    elif preset_key == "next" and next_class:
        preset = next_class.get("session_defaults")

    active_sessions = len(sessions)
    total_present = sum(session.get("present", 0) for session in sessions)
    total_tracked = sum(session.get("total_students", 0) for session in sessions)
    attendance_rate = round((total_present / total_tracked) * 100) if total_tracked else 0

    return {
        **_ui_shell_context(),
        "faculty_user": user,
        "overview": overview,
        "sessions": sessions,
        "session_form": _ui_faculty_form_defaults(user, form_values=form_values, preset=preset),
        "stats": {
            "active_sessions": active_sessions,
            "total_present": total_present,
            "total_tracked": total_tracked,
            "attendance_rate": attendance_rate,
        },
        "last_updated": timezone.localtime(),
    }


def _ui_student_context(request):
    student = Student.objects.select_related("department").filter(user=request.user).first()
    student_payload = _student_payload(student, request=request)
    overview = _student_overview_payload(student) if student else {
        "stats": {"total_classes": 0, "present": 0, "absent": 0, "attendance_percentage": 0},
        "subject_summary": [],
        "attendance_history": [],
    }
    normalized_section = ""
    if student and student.section and student.section != (student.department.name if student.department else None):
        normalized_section = student.section

    return {
        **_ui_shell_context(),
        "student_user": request.user,
        "student_info": student_payload,
        "overview": overview,
        "enrollment_section": normalized_section,
    }


def _resolve_ui_session_state(request, code):
    payload = {
        "state": "ready",
        "message": None,
        "tone": "slate",
        "session_info": None,
        "student_info": None,
    }

    try:
        session = AttendanceSession.objects.get(session_code=code, is_active=True)
    except AttendanceSession.DoesNotExist:
        payload.update({"state": "error", "message": "Session not found or inactive.", "tone": "rose"})
        return payload

    if _session_expired(session):
        session.is_active = False
        session.save(update_fields=["is_active"])
        payload.update({"state": "error", "message": "Session has expired.", "tone": "rose"})
        return payload

    student = Student.objects.select_related("department").filter(user=request.user).first()
    payload["session_info"] = _session_payload(request, session)
    payload["student_info"] = _student_payload(student, request=request)

    if request.user.is_staff:
        payload.update({"state": "error", "message": "Faculty accounts can monitor sessions, but attendance is marked from a student account.", "tone": "rose"})
        return payload

    if not student:
        payload.update({"state": "error", "message": "No student profile is linked to this account.", "tone": "rose"})
        return payload

    if not _student_can_access_session(student, session):
        payload.update({"state": "error", "message": "You are not eligible for this session.", "tone": "rose"})
        return payload

    if AttendanceRecord.objects.filter(student=student, session=session).exists():
        payload.update({"state": "error", "message": "Attendance has already been marked for this session.", "tone": "emerald"})
        return payload

    return payload


@ensure_csrf_cookie
def ui_home(request):
    context = {
        **_ui_shell_context(),
        **_ui_login_context(request.GET.get("role", "student")),
    }
    return render(request, "attendance/htmx/home.html", context)


@ensure_csrf_cookie
def ui_login(request):
    context = {
        **_ui_shell_context(),
        **_ui_login_context(request.GET.get("role", "student")),
    }
    return render(request, "attendance/htmx/login.html", context)


def ui_login_panel(request):
    return render(
        request,
        "attendance/htmx/_login_panel.html",
        _ui_login_context(request.GET.get("role", "student")),
    )


@ensure_csrf_cookie
def ui_admin(request):
    if not request.user.is_authenticated or not request.user.is_superuser:
        return redirect("/login?role=admin")

    if request.method == "POST":
        action = (request.POST.get("ui_action") or "").strip()
        form_values = request.POST

        try:
            if action == "create_timetable":
                values = _parse_timetable_payload(request.POST)
                TimetableEntry.objects.create(**values)
                messages.success(request, "Timetable entry created.")
                return redirect(request.path)

            if action == "delete_timetable":
                entry = get_object_or_404(TimetableEntry, pk=request.POST.get("entry_id"))
                entry.delete()
                messages.success(request, "Timetable entry deleted.")
                return redirect(request.path)

            if action == "create_student":
                values = _parse_student_payload(request.POST)
                created_user = User.objects.create_user(username=values["username"], password=values["password"])
                Student.objects.create(
                    user=created_user,
                    name=values["name"],
                    roll_number=values["roll_number"],
                    department=values["department"],
                    section=values["section"],
                    qr_mode=values["qr_mode"],
                )
                messages.success(request, "Student created.")
                return redirect(request.path)

            if action == "delete_student":
                student = get_object_or_404(Student.objects.select_related("user"), pk=request.POST.get("student_id"))
                _delete_student_and_user(student)
                messages.success(request, "Student deleted.")
                return redirect(request.path)

            if action == "clear_students":
                student_ids = list(Student.objects.values_list("id", flat=True))
                users_to_delete = list(
                    User.objects.filter(student__id__in=student_ids, is_staff=False, is_superuser=False).values_list("id", flat=True)
                )
                deleted_students = Student.objects.filter(id__in=student_ids).count()
                Student.objects.filter(id__in=student_ids).delete()
                if users_to_delete:
                    User.objects.filter(id__in=users_to_delete).delete()
                messages.success(request, f"Deleted {deleted_students} students.")
                return redirect(request.path)

            if action == "create_faculty":
                values = _parse_faculty_payload(request.POST)
                User.objects.create_user(
                    username=values["username"],
                    password=values["password"],
                    first_name=values["first_name"],
                    last_name=values["last_name"],
                    email=values["email"],
                    is_staff=True,
                    is_active=values["is_active"],
                )
                messages.success(request, "Faculty account created.")
                return redirect(request.path)

            if action == "delete_faculty":
                faculty_user = get_object_or_404(User, pk=request.POST.get("faculty_id"), is_staff=True, is_superuser=False)
                faculty_user.delete()
                messages.success(request, "Faculty deleted.")
                return redirect(request.path)

            messages.error(request, "Unknown admin action.")
        except ValueError as exc:
            messages.error(request, str(exc))
            return render(request, "attendance/htmx/admin_dashboard.html", _ui_admin_context(form_values), status=400)

    return render(request, "attendance/htmx/admin_dashboard.html", _ui_admin_context())


@ensure_csrf_cookie
def ui_faculty(request):
    if not request.user.is_authenticated or not request.user.is_staff or request.user.is_superuser:
        return redirect("/login?role=professor")

    if request.method == "POST":
        action = (request.POST.get("ui_action") or "").strip()
        form_values = request.POST

        try:
            if action == "create_session":
                department_name = (request.POST.get("department_name") or "").strip()
                section = (request.POST.get("section") or "").strip()
                semester = (request.POST.get("semester") or "").strip()
                subject = (request.POST.get("subject") or "").strip() or section or "General"
                attendance_date = (request.POST.get("attendance_date") or "").strip()
                attendance_time = (request.POST.get("attendance_time") or "").strip()

                if not section:
                    raise ValueError("Section is required to create a session.")

                parsed_date = None
                if attendance_date:
                    try:
                        parsed_date = datetime.strptime(attendance_date, "%Y-%m-%d").date()
                    except ValueError as exc:
                        raise ValueError("Attendance date must be in YYYY-MM-DD format.") from exc

                parsed_time = None
                if attendance_time:
                    try:
                        parsed_time = datetime.strptime(attendance_time, "%H:%M").time()
                    except ValueError as exc:
                        raise ValueError("Attendance time must be in HH:MM format.") from exc

                AttendanceSession.objects.create(
                    subject=subject,
                    department_name=department_name,
                    section=section,
                    semester=semester,
                    attendance_date=parsed_date,
                    attendance_time=parsed_time,
                    created_by=request.user,
                )
                messages.success(request, "Session created. Students will appear below and turn green as they mark attendance.")
                return redirect(request.path)

            if action == "close_session":
                session = get_object_or_404(AttendanceSession, pk=request.POST.get("session_id"), created_by=request.user)
                session.is_active = False
                session.save(update_fields=["is_active"])
                summary = _session_payload(request, session)
                messages.success(
                    request,
                    f"Session closed: {summary['subject']} • Present {summary['present']}/{summary['total_students']} • Absent {summary['absent']}.",
                )
                return redirect(request.path)

            messages.error(request, "Unknown faculty action.")
        except ValueError as exc:
            messages.error(request, str(exc))
            return render(request, "attendance/htmx/faculty_dashboard.html", _ui_faculty_context(request, request.user, form_values=form_values), status=400)

    preset_key = (request.GET.get("preset") or "").strip().lower()
    return render(request, "attendance/htmx/faculty_dashboard.html", _ui_faculty_context(request, request.user, preset_key=preset_key))


@ensure_csrf_cookie
def ui_student(request):
    if not request.user.is_authenticated:
        return redirect("/login?role=student")
    if request.user.is_superuser:
        return redirect("/admin-dashboard")
    if request.user.is_staff:
        return redirect("/dashboard")

    student = Student.objects.select_related("department").filter(user=request.user).first()

    if request.method == "POST":
        action = (request.POST.get("ui_action") or "").strip()
        if action == "open_session":
            session_code = (request.POST.get("session_code") or "").strip()
            if not session_code:
                messages.error(request, "Enter the session code shared by your instructor.")
                return render(request, "attendance/htmx/student_dashboard.html", _ui_student_context(request), status=400)

            resolved_code = session_code
            if re.match(r"^https?://", session_code, re.IGNORECASE):
                try:
                    parsed_url = urlsplit(session_code)
                    path_segments = [segment for segment in parsed_url.path.split("/") if segment]
                    session_index = next((index for index, segment in enumerate(path_segments) if segment.lower() == "session"), -1)
                    if session_index >= 0 and session_index + 1 < len(path_segments):
                        resolved_code = path_segments[session_index + 1]
                    else:
                        raise ValueError()
                except Exception:
                    messages.error(request, "The session link is invalid. Paste the full session URL or the session code.")
                    return render(request, "attendance/htmx/student_dashboard.html", _ui_student_context(request), status=400)
            else:
                resolved_code = session_code.upper()

            return redirect(f"/session/{resolved_code}")

        if action == "enroll_face":
            if not student:
                messages.error(request, "No student profile is linked to this account.")
                return render(request, "attendance/htmx/student_dashboard.html", _ui_student_context(request), status=400)

            image_data = request.POST.get("image_data") or ""
            section = (request.POST.get("section") or "").strip()
            if not image_data:
                messages.error(request, "Capture a camera image before saving your face profile.")
                return render(request, "attendance/htmx/student_dashboard.html", _ui_student_context(request), status=400)
            if not section:
                messages.error(request, "Enter your section before saving your profile.")
                return render(request, "attendance/htmx/student_dashboard.html", _ui_student_context(request), status=400)

            payload = image_data.split(",", 1)[1] if "," in image_data else image_data
            try:
                raw_bytes = base64.b64decode(payload)
            except Exception:
                messages.error(request, "Invalid captured image data.")
                return render(request, "attendance/htmx/student_dashboard.html", _ui_student_context(request), status=400)

            student.face_encoding = None
            student.face_image.save(
                f"face_{student.roll_number}.jpg",
                ContentFile(raw_bytes),
                save=False,
            )
            student.section = section
            try:
                student.save()
            except Exception as exc:
                detail = "Could not save your face profile."
                if settings.DEBUG:
                    detail = f"{detail} {exc}"
                messages.error(request, detail)
                return render(request, "attendance/htmx/student_dashboard.html", _ui_student_context(request), status=400)

            messages.success(request, "Face profile saved. You can now join an attendance session.")
            return redirect(request.path)

        messages.error(request, "Unknown student action.")

    return render(request, "attendance/htmx/student_dashboard.html", _ui_student_context(request))


@ensure_csrf_cookie
def ui_session(request, code):
    if not request.user.is_authenticated:
        return redirect("/login?role=student")

    context = {
        **_ui_shell_context(),
        "session_code": code,
        **_resolve_ui_session_state(request, code),
    }
    return render(request, "attendance/htmx/student_session.html", context)


@require_POST
@ensure_csrf_cookie
def ui_login_submit(request):
    role = request.POST.get("role", "student")
    username = (request.POST.get("username") or "").strip()
    password = request.POST.get("password") or ""
    user = authenticate(request, username=username, password=password)

    error_message = None
    if user is None:
        error_message = "Invalid username or password."
    elif role == "admin" and not user.is_superuser:
        error_message = "Access denied. Please use an admin account."
    elif role == "professor" and not user.is_staff:
        error_message = "Access denied. Please use a faculty account."
    elif role == "student" and user.is_staff:
        error_message = "Access denied. Please use a student account."

    if error_message:
        response = render(request, "attendance/htmx/_login_panel.html", _ui_login_context(role, error_message), status=400)
        return response

    login(request, user)

    if user.is_superuser:
        destination = "/admin-dashboard"
    elif user.is_staff:
        destination = "/dashboard"
    else:
        destination = "/student"

    if request.headers.get("HX-Request"):
        response = HttpResponse(status=200)
        response["HX-Redirect"] = destination
        return response

    return redirect(destination)


def ensure_default_departments():
    """Ensure basic departments exist so the registration form has options."""
    for name in ["MBA", "MCA"]:
        Department.objects.get_or_create(name=name)


def _normalized_value(value):
    return (value or "").strip().casefold()


def _students_for_session(session):
    section_value = _normalized_value(session.section)
    department_value = _normalized_value(getattr(session, "department_name", ""))
    students = Student.objects.all()
    if department_value:
        students = students.filter(department__name__iexact=session.department_name)

    if not section_value:
        return students

    if department_value:
        return students.filter(
            models.Q(section__iexact=session.section)
            | models.Q(section__iexact=session.department_name)
            | models.Q(section__isnull=True)
            | models.Q(section="")
        )

    return students.filter(models.Q(section__iexact=session.section))


def _student_can_access_session(student, session):
    section_value = _normalized_value(session.section)
    student_section = _normalized_value(student.section)
    department_name = _normalized_value(student.department.name if student.department else "")
    session_department = _normalized_value(getattr(session, "department_name", ""))

    # Older student records stored the department name in the section field.
    # Treat that legacy value as "section not set" so valid department matches still work.
    if student_section and department_name and student_section == department_name:
        student_section = ""

    department_matches = True if not session_department else department_name == session_department
    if not section_value:
        section_matches = True
    elif student_section:
        section_matches = student_section == section_value
    else:
        section_matches = bool(session_department and department_matches)

    return department_matches and section_matches


def _frontend_base_url(request):
    origin = request.headers.get("Origin")
    if origin:
        return origin.rstrip("/")

    referer = request.headers.get("Referer")
    if referer:
        parsed = urlsplit(referer)
        if parsed.scheme and parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}"

    return request.build_absolute_uri("/").rstrip("/")


def _session_join_path(session):
    return f"/session/{session.session_code}"


def _session_join_url(request, session):
    if request is None:
        return _session_join_path(session)
    return f"{_frontend_base_url(request)}{_session_join_path(session)}"


def _student_qr_payload(student):
    if getattr(student, "qr_mode", Student.QR_MODE_PERMANENT) == Student.QR_MODE_DAILY:
        return student.daily_qr_payload()
    return signing.dumps({"roll_number": student.roll_number, "mode": Student.QR_MODE_PERMANENT}, salt=QR_CREDENTIAL_SALT, compress=True)


def _active_student_qr_payload(student):
    if getattr(student, "qr_mode", Student.QR_MODE_PERMANENT) == Student.QR_MODE_DAILY:
        return student.daily_qr_payload()
    return student.permanent_qr_payload()


def _qr_image_bytes(payload):
    temporary_student = Student(name="temp", roll_number="temp")
    qr_bytes = temporary_student._build_qr_content(payload)
    return qr_bytes or b""


def _qr_payload_matches_student(student, qr_payload):
    if not qr_payload:
        return False

    if str(qr_payload).strip() == str(student.roll_number):
        return True

    try:
        payload = signing.loads(qr_payload, salt=QR_CREDENTIAL_SALT)
    except signing.BadSignature:
        return False

    payload_roll = str(payload.get("roll_number") or "").strip()
    if payload_roll != str(student.roll_number):
        return False

    if payload.get("mode") == Student.QR_MODE_DAILY:
        return str(payload.get("date") or "").strip() == timezone.localdate().isoformat()

    return True


def _student_payload(student, request=None):
    if not student:
        return {
            "has_student_profile": False,
            "name": None,
            "roll_number": None,
            "section": None,
            "department": None,
            "has_face_profile": False,
            "has_qr_profile": False,
            "qr_mode": Student.QR_MODE_PERMANENT,
            "qr_code_url": None,
            "qr_code_data_url": None,
            "face_image_url": None,
            "qr_payload": None,
        }

    if student.qr_mode == Student.QR_MODE_PERMANENT and not student.qr_code:
        student._generate_qr(student.permanent_qr_payload())
        student.save(update_fields=["qr_code"])

    qr_code_url = request.build_absolute_uri(reverse("attendance:api_student_qr_image")) if request else None
    qr_code_data_url = None
    qr_bytes = _qr_image_bytes(_active_student_qr_payload(student))
    if qr_bytes:
        qr_code_data_url = f"data:image/png;base64,{base64.b64encode(qr_bytes).decode('ascii')}"
    face_image_url = request.build_absolute_uri(student.face_image.url) if request and student.face_image else None

    return {
        "has_student_profile": True,
        "name": student.name,
        "roll_number": student.roll_number,
        "section": student.section,
        "department": student.department.name if student.department else None,
        "has_face_profile": bool(student.face_image and student.face_encoding),
        "has_qr_profile": True,
        "qr_mode": student.qr_mode,
        "qr_code_url": qr_code_url,
        "qr_code_data_url": qr_code_data_url,
        "face_image_url": face_image_url,
        "qr_payload": _active_student_qr_payload(student),
    }


def _serialize_attendance_record(record):
    return {
        "id": record.id,
        "student_name": record.student.name,
        "roll_number": record.student.roll_number,
        "method": record.method,
        "status": record.status,
        "created_at": record.created_at.isoformat(),
        "time_label": timezone.localtime(record.created_at).strftime("%I:%M:%S %p"),
    }


def _session_student_tiles(session):
    students = list(_students_for_session(session).select_related("department", "user").order_by("name", "roll_number"))
    attendance_map = {
        record.student_id: record
        for record in AttendanceRecord.objects.filter(session=session).select_related("student")
    }

    return [
        {
            "id": student.id,
            "name": student.name,
            "roll_number": student.roll_number,
            "department_name": student.department.name if student.department else "",
            "section": student.section or "",
            "present": student.id in attendance_map,
            "status": AttendanceRecord.STATUS_PRESENT if student.id in attendance_map else AttendanceRecord.STATUS_ABSENT,
            "marked_at": attendance_map[student.id].created_at.isoformat() if student.id in attendance_map else None,
            "time_label": timezone.localtime(attendance_map[student.id].created_at).strftime("%I:%M:%S %p") if student.id in attendance_map else None,
            "method": attendance_map[student.id].method if student.id in attendance_map else None,
        }
        for student in students
    ]


def _session_payload(request, session):
    student_tiles = _session_student_tiles(session)
    total_students = len(student_tiles)
    attendance_qs = AttendanceRecord.objects.filter(session=session).select_related("student").order_by("-created_at")
    present = attendance_qs.count()
    absent = max(total_students - present, 0)
    recent_records = list(attendance_qs[:5])
    last_activity = recent_records[0].created_at if recent_records else session.start_time

    return {
        "id": session.id,
        "session_code": session.session_code,
        "department_name": session.department_name,
        "section": session.section,
        "semester": session.semester,
        "subject": session.subject,
        "created_at": session.start_time.isoformat(),
        "attendance_date": session.attendance_date.isoformat() if session.attendance_date else None,
        "attendance_time": session.attendance_time.isoformat() if session.attendance_time else None,
        "total_students": total_students,
        "present": present,
        "absent": absent,
        "attendance_rate": round((present / total_students) * 100, 1) if total_students else 0,
        "join_path": _session_join_path(session),
        "join_url": _session_join_url(request, session),
        "verification_mode": "qr_face",
        "last_activity_at": last_activity.isoformat(),
        "recent_attendance": [_serialize_attendance_record(record) for record in recent_records],
        "student_tiles": student_tiles,
    }


def _time_label(value):
    return value.strftime("%H:%M") if value else None


def _serialize_timetable_entry(entry):
    return {
        "id": entry.id,
        "faculty_id": entry.faculty_id,
        "faculty_username": entry.faculty.username,
        "program": entry.program,
        "department_id": entry.department_id,
        "department_name": entry.department.name if entry.department else "",
        "semester": entry.semester,
        "section": entry.section,
        "subject": entry.subject,
        "day_of_week": entry.day_of_week,
        "start_time": _time_label(entry.start_time),
        "end_time": _time_label(entry.end_time),
        "is_active": entry.is_active,
    }


def _students_for_timetable_entry(entry):
    students = Student.objects.select_related("department")
    if entry.department_id:
        students = students.filter(department=entry.department)

    if not entry.section:
        return students

    department_name = entry.department.name if entry.department else ""
    if department_name:
        return students.filter(
            models.Q(section__iexact=entry.section)
            | models.Q(section__iexact=department_name)
            | models.Q(section__isnull=True)
            | models.Q(section="")
        )

    return students.filter(models.Q(section__iexact=entry.section))


def _session_has_started(session):
    current_date = timezone.localdate()
    current_time = timezone.localtime().time()

    if session.attendance_date:
        if session.attendance_date < current_date:
            return True
        if session.attendance_date > current_date:
            return False

    scheduled_time = session.attendance_time or timezone.localtime(session.start_time).time()
    return scheduled_time <= current_time


def _faculty_session_for_timetable(user, entry):
    sessions = AttendanceSession.objects.filter(is_active=True, created_by=user, subject__iexact=entry.subject)
    if entry.department_id:
        sessions = sessions.filter(department_name__iexact=entry.department.name)
    if entry.section:
        sessions = sessions.filter(section__iexact=entry.section)
    if entry.semester:
        sessions = sessions.filter(semester__iexact=entry.semester)
    return sessions.order_by("-start_time").first()


def _faculty_overview_payload(request, user):
    now = timezone.localtime()
    today_name = now.strftime("%A")
    timetable = list(
        TimetableEntry.objects.select_related("department", "faculty")
        .filter(faculty=user, is_active=True, day_of_week=today_name)
        .order_by("start_time")
    )

    current_entry = next((entry for entry in timetable if entry.start_time <= now.time() <= entry.end_time), None)
    next_entry = next((entry for entry in timetable if entry.start_time > now.time()), None)

    def build_class_payload(entry, current=False):
        if not entry:
            return None

        active_session = _faculty_session_for_timetable(user, entry)
        total_students = _students_for_timetable_entry(entry).count()
        present = AttendanceRecord.objects.filter(session=active_session).count() if active_session else 0
        absent = max(total_students - present, 0)

        return {
            **_serialize_timetable_entry(entry),
            "is_current": current,
            "total_students": total_students,
            "present": present,
            "absent": absent,
            "attendance_rate": round((present / total_students) * 100, 1) if total_students else 0,
            "active_session": _session_payload(request, active_session) if active_session else None,
            "session_defaults": {
                "department_name": entry.department.name if entry.department else "",
                "section": entry.section,
                "semester": entry.semester,
                "subject": entry.subject,
                "attendance_date": timezone.localdate().isoformat(),
                "attendance_time": _time_label(entry.start_time),
            },
        }

    active_sessions = AttendanceSession.objects.filter(is_active=True, created_by=user).order_by("-start_time")[:5]
    records_today = AttendanceRecord.objects.filter(date=timezone.localdate(), session__created_by=user).count()

    return {
        "current_class": build_class_payload(current_entry, current=True),
        "next_class": build_class_payload(next_entry, current=False),
        "today_schedule": [_serialize_timetable_entry(entry) for entry in timetable],
        "active_sessions": [_session_payload(request, session) for session in active_sessions],
        "stats": {
            "today_classes": len(timetable),
            "active_sessions": AttendanceSession.objects.filter(is_active=True, created_by=user).count(),
            "attendance_events_today": records_today,
        },
    }


def _student_overview_payload(student):
    candidate_sessions = list(
        AttendanceSession.objects.select_related("created_by")
        .order_by("-attendance_date", "-attendance_time", "-start_time")
    )
    eligible_sessions = [session for session in candidate_sessions if _student_can_access_session(student, session)]
    started_sessions = [session for session in eligible_sessions if _session_has_started(session)]
    session_ids = [session.id for session in started_sessions]
    session_by_id = {session.id: session for session in started_sessions}

    records = list(
        AttendanceRecord.objects.filter(student=student, session_id__in=session_ids)
        .select_related("session")
        .order_by("-date", "-time", "-created_at")
    )
    present_session_ids = {record.session_id for record in records}

    subject_summary = {}
    for session in started_sessions:
        bucket = subject_summary.setdefault(
            session.subject,
            {"subject": session.subject, "total": 0, "present": 0, "absent": 0, "percentage": 0},
        )
        bucket["total"] += 1
        if session.id in present_session_ids:
            bucket["present"] += 1

    for bucket in subject_summary.values():
        bucket["absent"] = max(bucket["total"] - bucket["present"], 0)
        bucket["percentage"] = round((bucket["present"] / bucket["total"]) * 100, 1) if bucket["total"] else 0

    history = []
    for record in records[:12]:
        session = session_by_id.get(record.session_id)
        history.append(
            {
                "id": record.id,
                "subject": session.subject if session else (record.subject.name if record.subject else "General"),
                "date": record.date.isoformat() if record.date else None,
                "time": record.time.isoformat() if record.time else None,
                "status": record.status,
                "method": record.method,
                "section": session.section if session else student.section,
                "semester": session.semester if session else "",
            }
        )

    total_classes = len(started_sessions)
    present_classes = len(present_session_ids)
    absent_classes = max(total_classes - present_classes, 0)

    return {
        "stats": {
            "total_classes": total_classes,
            "present": present_classes,
            "absent": absent_classes,
            "attendance_percentage": round((present_classes / total_classes) * 100, 1) if total_classes else 0,
        },
        "subject_summary": sorted(subject_summary.values(), key=lambda item: item["subject"].lower()),
        "attendance_history": history,
    }


def _admin_overview_payload():
    recent_records = AttendanceRecord.objects.select_related("student", "session").order_by("-created_at")[:10]
    recent_closed_sessions = AttendanceSession.objects.filter(is_active=False).order_by("-start_time")[:10]
    by_department = []
    for department in Department.objects.order_by("name"):
        student_count = Student.objects.filter(department=department).count()
        attendance_count = AttendanceRecord.objects.filter(student__department=department).count()
        by_department.append(
            {
                "department": department.name,
                "students": student_count,
                "attendance_events": attendance_count,
            }
        )

    return {
        "stats": {
            "students": Student.objects.count(),
            "faculty": User.objects.filter(is_staff=True, is_superuser=False).count(),
            "departments": Department.objects.count(),
            "active_sessions": AttendanceSession.objects.filter(is_active=True).count(),
            "timetable_entries": TimetableEntry.objects.filter(is_active=True).count(),
            "attendance_today": AttendanceRecord.objects.filter(date=timezone.localdate()).count(),
        },
        "department_summary": by_department,
        "recent_attendance": [
            {
                "id": record.id,
                "student_name": record.student.name,
                "subject": record.session.subject if record.session else (record.subject.name if record.subject else "General"),
                "date": record.date.isoformat() if record.date else None,
                "time": record.time.isoformat() if record.time else None,
                "method": record.method,
            }
            for record in recent_records
        ],
        "recent_closed_sessions": [
            _session_payload(None, session) for session in recent_closed_sessions
        ],
    }


def _serialize_student(student):
    return {
        "id": student.id,
        "name": student.name,
        "roll_number": student.roll_number,
        "department_id": student.department_id,
        "department_name": student.department.name if student.department else "",
        "section": student.section or "",
        "qr_mode": student.qr_mode,
        "username": student.user.username if student.user else "",
        "has_face_profile": bool(student.face_image and student.face_encoding),
    }


def _student_options_payload():
    return {
        "departments": list(Department.objects.order_by("name").values("id", "name")),
        "qr_modes": [
            {"value": Student.QR_MODE_PERMANENT, "label": "Permanent QR for ID card"},
            {"value": Student.QR_MODE_DAILY, "label": "Random QR for current day"},
        ],
    }


def _serialize_faculty(user):
    return {
        "id": user.id,
        "username": user.username,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "email": user.email,
        "is_active": user.is_active,
    }


def _normalize_student_username(value):
    base = "".join(character.lower() if character.isalnum() else "_" for character in (value or "").strip()).strip("_")
    return base[:150]


def _parse_student_payload(payload, existing_student=None):
    name = (payload.get("name") or "").strip()
    roll_number = (payload.get("roll_number") or "").strip()
    section = (payload.get("section") or "").strip()
    password = payload.get("password") or ""
    username = (payload.get("username") or "").strip()
    department_id = payload.get("department_id")
    qr_mode = (payload.get("qr_mode") or Student.QR_MODE_PERMANENT).strip()

    if not name or not roll_number:
        raise ValueError("Name and roll number are required.")

    if not existing_student and not password:
        raise ValueError("Password is required.")

    if qr_mode not in [Student.QR_MODE_PERMANENT, Student.QR_MODE_DAILY]:
        raise ValueError("QR mode is invalid.")

    username = _normalize_student_username(username or roll_number or name)
    if not username:
        raise ValueError("Username is required.")

    username_qs = User.objects.filter(username=username)
    if existing_student and existing_student.user_id:
        username_qs = username_qs.exclude(pk=existing_student.user_id)
    if username_qs.exists():
        raise ValueError("That username already exists.")

    roll_qs = Student.objects.filter(roll_number=roll_number)
    if existing_student:
        roll_qs = roll_qs.exclude(pk=existing_student.pk)
    if roll_qs.exists():
        raise ValueError("That roll number already exists.")

    department = None
    if department_id:
        department = Department.objects.filter(pk=department_id).first()
        if not department:
            raise ValueError("Selected department was not found.")

    return {
        "name": name,
        "roll_number": roll_number,
        "section": section,
        "password": password,
        "username": username,
        "department": department,
        "qr_mode": qr_mode,
    }


def _parse_faculty_payload(payload, existing_user=None):
    username = _normalize_student_username((payload.get("username") or "").strip())
    password = payload.get("password") or ""
    first_name = (payload.get("first_name") or "").strip()
    last_name = (payload.get("last_name") or "").strip()
    email = (payload.get("email") or "").strip()
    is_active = bool(payload.get("is_active", True))

    if not username:
        raise ValueError("Username is required.")
    if not existing_user and not password:
        raise ValueError("Password is required.")

    username_qs = User.objects.filter(username=username)
    if existing_user:
        username_qs = username_qs.exclude(pk=existing_user.pk)
    if username_qs.exists():
        raise ValueError("That username already exists.")

    return {
        "username": username,
        "password": password,
        "first_name": first_name,
        "last_name": last_name,
        "email": email,
        "is_active": is_active,
    }


def _delete_student_and_user(student):
    linked_user = student.user if student.user and not student.user.is_staff and not student.user.is_superuser else None
    student.delete()
    if linked_user:
        linked_user.delete()


def _parse_timetable_payload(payload):
    faculty_id = payload.get("faculty_id")
    subject = (payload.get("subject") or "").strip()
    section = (payload.get("section") or "").strip()
    day_of_week = (payload.get("day_of_week") or "").strip().title()
    start_time = (payload.get("start_time") or "").strip()
    end_time = (payload.get("end_time") or "").strip()
    semester = (payload.get("semester") or "").strip()
    program = (payload.get("program") or "").strip()
    department_id = payload.get("department_id") or None

    if not faculty_id or not subject or not section or not day_of_week or not start_time or not end_time:
        raise ValueError("Faculty, subject, section, day, start time, and end time are required.")

    if day_of_week not in DAY_SEQUENCE:
        raise ValueError("Day of week is invalid.")

    try:
        start_value = datetime.strptime(start_time, "%H:%M").time()
        end_value = datetime.strptime(end_time, "%H:%M").time()
    except ValueError as exc:
        raise ValueError("Time must use HH:MM format.") from exc

    if start_value >= end_value:
        raise ValueError("End time must be after start time.")

    faculty = User.objects.filter(pk=faculty_id, is_staff=True).first()
    if not faculty:
        raise ValueError("Selected faculty member was not found.")

    department = None
    if department_id:
        department = Department.objects.filter(pk=department_id).first()
        if not department:
            raise ValueError("Selected department was not found.")

    return {
        "faculty": faculty,
        "program": program,
        "department": department,
        "semester": semester,
        "section": section,
        "subject": subject,
        "day_of_week": day_of_week,
        "start_time": start_value,
        "end_time": end_value,
        "is_active": bool(payload.get("is_active", True)),
    }


def _timetable_options_payload():
    faculty = list(User.objects.filter(is_staff=True).order_by("username").values("id", "username"))
    departments = list(Department.objects.order_by("name").values("id", "name"))
    return {"faculty": faculty, "departments": departments}


def _decode_base64_frame(image_data):
    if not image_data:
        raise ValueError("Missing image data.")

    if "," in image_data:
        image_data = image_data.split(",", 1)[1]

    try:
        raw_bytes = base64.b64decode(image_data)
    except Exception as exc:
        raise ValueError("Invalid image data.") from exc

    try:
        import cv2
        import numpy as np
    except ImportError as exc:
        raise RuntimeError("Required libraries not installed.") from exc

    image_array = np.frombuffer(raw_bytes, dtype=np.uint8)
    frame = cv2.imdecode(image_array, cv2.IMREAD_COLOR)
    if frame is None:
        raise ValueError("Could not decode image.")

    return frame, cv2, np


def _verify_face_match(student, image_data):
    if not student.face_encoding:
        return False, "Face recognition data missing."

    try:
        frame, cv2, np = _decode_base64_frame(image_data)
    except ValueError as exc:
        return False, str(exc)
    except RuntimeError as exc:
        return False, str(exc)

    try:
        import os
        import tempfile

        os.environ.setdefault("TF_USE_LEGACY_KERAS", "1")
        from deepface import DeepFace
    except ImportError as exc:
        return False, f"Face recognition library not installed: {exc}."

    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
        tmp_path = tmp.name
        cv2.imwrite(tmp_path, frame)

    try:
        live_result = DeepFace.represent(img_path=tmp_path, model_name="Facenet", enforce_detection=False)
    except Exception:
        live_result = []
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass

    if not live_result:
        return False, "Face recognition data missing."

    live_embedding = np.array(live_result[0]["embedding"])
    known_embedding = np.array(student.face_encoding)
    live_norm = np.linalg.norm(live_embedding)
    known_norm = np.linalg.norm(known_embedding)

    if live_norm == 0 or known_norm == 0:
        return False, "Face recognition data missing."

    cos_sim = np.dot(live_embedding, known_embedding) / (live_norm * known_norm)
    if cos_sim < 0.85:
        return False, "Face does not match."

    return True, None


def _issue_api_token(user):
    return signing.dumps({"user_id": user.id}, salt=API_AUTH_TOKEN_SALT, compress=True)


def _resolve_api_user(request):
    auth_header = request.headers.get("Authorization") or ""
    if auth_header.startswith("Bearer "):
        token = auth_header.split(" ", 1)[1].strip()
        try:
            payload = signing.loads(token, salt=API_AUTH_TOKEN_SALT, max_age=60 * 60 * 12)
            return User.objects.filter(pk=payload.get("user_id")).first()
        except signing.BadSignature:
            return None
        except signing.SignatureExpired:
            return None

    if request.user.is_authenticated:
        return request.user

    return None


@ensure_csrf_cookie
def home(request):
    # Homepage with role-based login buttons (admin/professor/student).
    # In the new React frontend, this can be replaced by the SPA entry point.
    return render(request, "attendance/home.html")


def dashboard(request):
    today = timezone.localdate()
    attendance_records = AttendanceRecord.objects.filter(date=today).select_related("student")
    students = Student.objects.all()

    present_student_ids = {r.student_id for r in attendance_records}

    context = {
        "today": today,
        "attendance_records": attendance_records,
        "students": students,
        "present_student_ids": present_student_ids,
    }
    return render(request, "attendance/dashboard.html", context)


@staff_member_required
def professor_dashboard(request):
    # List active sessions and quick stats for the professor
    active_sessions = AttendanceSession.objects.filter(is_active=True)

    session_stats = []
    for session in active_sessions:
        total_students = _students_for_session(session).count()
        present = AttendanceRecord.objects.filter(session=session).count()
        percentage = (present / total_students * 100) if total_students else 0
        session_stats.append(
            {
                "session": session,
                "total": total_students,
                "present": present,
                "percentage": round(percentage, 1),
            }
        )

    if request.GET.get("ajax"):
        return render(request, "attendance/professor_dashboard_live.html", {"session_stats": session_stats})

    return render(
        request,
        "attendance/professor_dashboard.html",
        {"session_stats": session_stats, "active_sessions": active_sessions},
    )


@require_POST
@staff_member_required
def deactivate_session(request, code):
    session = get_object_or_404(AttendanceSession, session_code=code)
    session.is_active = False
    session.save(update_fields=["is_active"])
    messages.success(request, "Session has been ended.")
    return redirect("attendance:professor_dashboard")


@staff_member_required
def create_session(request):
    if request.method == "POST":
        form = AttendanceSessionForm(request.POST)
        if form.is_valid():
            session = form.save(commit=False)
            session.created_by = request.user
            session.save()
            messages.success(
                request,
                f"Session created: {session.subject} ({session.section}) - Code {session.session_code}",
            )
            return redirect("attendance:professor_dashboard")
    else:
        form = AttendanceSessionForm()

    return render(request, "attendance/create_session.html", {"form": form})


def _session_expired(session: AttendanceSession, window_hours: int = 2) -> bool:
    return timezone.now() > session.start_time + timedelta(hours=window_hours)


@login_required
def student_session(request, code):
    session = get_object_or_404(AttendanceSession, session_code=code)

    if not session.is_active or _session_expired(session):
        return render(request, "attendance/session_invalid.html", {"message": "Session is not active or has expired."})

    try:
        student = Student.objects.get(user=request.user)
    except Student.DoesNotExist:
        return render(request, "attendance/session_invalid.html", {"message": "No student profile found for this account."})

    if not _student_can_access_session(student, session):
        return render(
            request,
            "attendance/session_invalid.html",
            {"message": "You are not eligible for this session."},
        )

    if AttendanceRecord.objects.filter(student=student, session=session).exists():
        return render(request, "attendance/session_already_marked.html", {"session": session})

    return render(request, "attendance/student_session.html", {"session": session, "student": student})


@require_POST
def mark_dual_attendance(request, code):
    try:
        session = AttendanceSession.objects.get(session_code=code, is_active=True)
    except AttendanceSession.DoesNotExist:
        return JsonResponse({"success": False, "message": "Session not found or inactive."}, status=404)

    if _session_expired(session):
        session.is_active = False
        session.save(update_fields=["is_active"])
        return JsonResponse({"success": False, "message": "Session has expired."}, status=400)

    payload = json.loads(request.body.decode("utf-8"))
    roll = payload.get("roll")
    face_image = payload.get("face_image")

    if not roll or not face_image:
        return JsonResponse({"success": False, "message": "Missing roll or face image."}, status=400)

    try:
        student = Student.objects.get(roll_number=roll)
    except Student.DoesNotExist:
        return JsonResponse({"success": False, "message": "Student not found."}, status=404)

    # ensure student belongs to session section
    if not _student_can_access_session(student, session):
        return JsonResponse({"success": False, "message": "Student not authorized for this session."}, status=403)

    if AttendanceRecord.objects.filter(student=student, session=session).exists():
        return JsonResponse({"success": False, "message": "Already marked attendance for this session."}, status=400)

    face_ok, face_error = _verify_face_match(student, face_image)
    if not face_ok:
        return JsonResponse({"success": False, "message": face_error}, status=400)

    AttendanceRecord.objects.create(
        student=student,
        session=session,
        subject=None,
        date=timezone.localdate(),
        time=timezone.localtime().time(),
        status=AttendanceRecord.STATUS_PRESENT,
        method=AttendanceRecord.METHOD_QR_FACE,
    )

    return JsonResponse({"success": True, "message": "Attendance marked successfully."})


@staff_member_required
def student_register(request):
    ensure_default_departments()

    if request.method == "POST":
        form = StudentRegistrationForm(request.POST, request.FILES)
        if form.is_valid():
            student = form.save(commit=False)

            live_photo_data = form.cleaned_data.get("live_photo_data")
            if live_photo_data:
                if "," in live_photo_data:
                    live_photo_data = live_photo_data.split(",", 1)[1]
                try:
                    raw_bytes = base64.b64decode(live_photo_data)
                    student.face_image.save(
                        f"face_{student.roll_number}.jpg",
                        ContentFile(raw_bytes),
                        save=False,
                    )
                except Exception:
                    pass

            from secrets import token_urlsafe

            generated_password = token_urlsafe(8)
            existing = User.objects.filter(username=student.roll_number).first()
            if existing:
                form.add_error("roll_number", "A user with this roll number already exists.")
                return render(request, "attendance/student_register.html", {"form": form})

            user = Student.create_user_for_student(student.roll_number, generated_password)
            student.user = user

            student.save()
            messages.success(
                request,
                f"Student {student.name} registered successfully. Password: {generated_password}",
            )
            return redirect("attendance:student_list")
    else:
        form = StudentRegistrationForm()

    return render(request, "attendance/student_register.html", {"form": form})


def faculty_register(request):
    if request.method == "POST":
        form = FacultyRegistrationForm(request.POST)
        if form.is_valid():
            username = form.cleaned_data.get("username")
            password = form.cleaned_data.get("password")

            if User.objects.filter(username=username).exists():
                form.add_error("username", "A user with this username already exists.")
            else:
                user = User.objects.create_user(username=username, password=password, is_staff=True)
                messages.success(request, f"Faculty account created: {username}")
                return redirect("attendance:home")
    else:
        form = FacultyRegistrationForm()

    return render(request, "attendance/faculty_register.html", {"form": form})


@login_required
def student_edit(request, student_id):
    student = get_object_or_404(Student, pk=student_id)

    if request.method == "POST":
        form = StudentEditForm(request.POST, request.FILES, instance=student)
        if form.is_valid():
            student = form.save(commit=False)

            live_photo_data = form.cleaned_data.get("live_photo_data")
            if live_photo_data:
                if "," in live_photo_data:
                    live_photo_data = live_photo_data.split(",", 1)[1]
                try:
                    raw_bytes = base64.b64decode(live_photo_data)
                    student.face_image.save(
                        f"face_{student.roll_number}.jpg",
                        ContentFile(raw_bytes),
                        save=False,
                    )
                except Exception:
                    pass

            student.save()
            messages.success(request, f"Student {student.name} updated successfully.")
            return redirect("attendance:student_list")
    else:
        form = StudentEditForm(instance=student)

    return render(request, "attendance/student_register.html", {"form": form, "editing": True})


@staff_member_required
def student_list(request):
    students = Student.objects.all().order_by("roll_number")
    today = timezone.localdate()
    records = AttendanceRecord.objects.filter(date=today)
    present = {r.student_id: r for r in records}
    return render(request, "attendance/student_list.html", {"students": students, "present": present})


def _htmx_redirect(url):
    response = HttpResponse(status=200)
    response["HX-Redirect"] = url
    return response


def _render_login(request, role="student"):
    if request.method == "POST":
        username = request.POST.get("username")
        password = request.POST.get("password")
        user = authenticate(request, username=username, password=password)
        if user is None:
            messages.error(request, "Invalid username or password.")
        else:
            if role == "professor" and not user.is_staff:
                messages.error(request, "Access denied. Please login with a professor account.")
            else:
                login(request, user)
                if role == "professor":
                    destination = "attendance:professor_dashboard"
                else:
                    destination = "attendance:student_dashboard"

            if request.headers.get("HX-Request"):
                return _htmx_redirect(redirect(destination).url)
            return redirect(destination)

    return render(request, "attendance/login_form.html", {"role": role})


def student_login(request):
    return _render_login(request, role="student")


def professor_login(request):
    return _render_login(request, role="professor")


def login_partial(request):
    role = request.GET.get("role", "student")
    return render(request, "attendance/login_form.html", {"role": role})


@require_POST
def api_login(request):
    payload = json.loads(request.body.decode("utf-8"))
    username = payload.get("username")
    password = payload.get("password")

    user = authenticate(request, username=username, password=password)
    if user is None:
        return JsonResponse({"success": False, "message": "Invalid credentials."}, status=401)

    login(request, user)
    return JsonResponse({
        "success": True,
        "username": user.username,
        "is_staff": user.is_staff,
        "is_superuser": user.is_superuser,
        "token": _issue_api_token(user),
    })


@require_POST
def api_logout(request):
    logout(request)
    return JsonResponse({"success": True})


@ensure_csrf_cookie
def api_csrf(request):
    """Ensure a CSRF cookie is set for SPA clients."""
    return JsonResponse({"csrfToken": get_token(request)})


def api_user(request):
    user = _resolve_api_user(request)
    if not user:
        return JsonResponse({"authenticated": False}, status=401)

    student = Student.objects.select_related("department").filter(user=user).first()
    student_payload = _student_payload(student, request=request)

    return JsonResponse(
        {
            "authenticated": True,
            "username": user.username,
            "is_staff": user.is_staff,
            "is_superuser": user.is_superuser,
            "student": student_payload,
        }
    )


def api_student_qr_image(request):
    user = _resolve_api_user(request)
    if not user:
        return JsonResponse({"detail": "Unauthorized"}, status=401)

    student = Student.objects.select_related("department").filter(user=user).first()
    if not student:
        return JsonResponse({"detail": "No student profile is linked to this account."}, status=404)

    payload = _active_student_qr_payload(student)
    qr_bytes = _qr_image_bytes(payload)
    if not qr_bytes:
        return JsonResponse({"detail": "QR generation is unavailable."}, status=500)

    response = HttpResponse(qr_bytes, content_type="image/png")
    response["Cache-Control"] = "no-store, max-age=0"
    return response


def api_faculty_dashboard_overview(request):
    user = _resolve_api_user(request)
    if not user or not user.is_staff:
        return JsonResponse({"detail": "Unauthorized"}, status=401)

    return JsonResponse(_faculty_overview_payload(request, user))


def api_student_dashboard_overview(request):
    user = _resolve_api_user(request)
    if not user:
        return JsonResponse({"detail": "Unauthorized"}, status=401)

    student = Student.objects.select_related("department").filter(user=user).first()
    if not student:
        return JsonResponse({"detail": "No student profile is linked to this account."}, status=404)

    return JsonResponse(_student_overview_payload(student))


def api_admin_dashboard_overview(request):
    user = _resolve_api_user(request)
    if not user or not user.is_superuser:
        return JsonResponse({"detail": "Unauthorized"}, status=401)

    return JsonResponse(_admin_overview_payload())


def api_admin_students(request):
    user = _resolve_api_user(request)
    if not user or not user.is_superuser:
        return JsonResponse({"detail": "Unauthorized"}, status=401)

    if request.method == "GET":
        students = Student.objects.select_related("department", "user").order_by("name", "roll_number")
        return JsonResponse({"students": [_serialize_student(student) for student in students], **_student_options_payload()})

    if request.method == "POST":
        payload = json.loads(request.body.decode("utf-8"))
        try:
            values = _parse_student_payload(payload)
        except ValueError as exc:
            return JsonResponse({"detail": str(exc)}, status=400)

        created_user = User.objects.create_user(username=values["username"], password=values["password"])
        student = Student.objects.create(
            user=created_user,
            name=values["name"],
            roll_number=values["roll_number"],
            department=values["department"],
            section=values["section"],
            qr_mode=values["qr_mode"],
        )
        return JsonResponse(_serialize_student(student), status=201)

    return JsonResponse({"detail": "Method not allowed."}, status=405)


def api_admin_student_detail(request, pk):
    user = _resolve_api_user(request)
    if not user or not user.is_superuser:
        return JsonResponse({"detail": "Unauthorized"}, status=401)

    student = get_object_or_404(Student.objects.select_related("department", "user"), pk=pk)

    if request.method == "GET":
        return JsonResponse(_serialize_student(student))

    if request.method in ["PUT", "PATCH"]:
        payload = json.loads(request.body.decode("utf-8"))
        try:
            values = _parse_student_payload(payload, existing_student=student)
        except ValueError as exc:
            return JsonResponse({"detail": str(exc)}, status=400)

        linked_user = student.user
        if linked_user is None:
            linked_user = User.objects.create_user(username=values["username"], password=values["password"] or get_random_string(12))
            student.user = linked_user
        else:
            linked_user.username = values["username"]
            if values["password"]:
                linked_user.set_password(values["password"])
            linked_user.save()

        student.name = values["name"]
        student.roll_number = values["roll_number"]
        student.department = values["department"]
        student.section = values["section"]
        student.qr_mode = values["qr_mode"]
        if student.qr_mode == Student.QR_MODE_PERMANENT:
            student.qr_code = None
        student.save()
        return JsonResponse(_serialize_student(student))

    if request.method == "DELETE":
        _delete_student_and_user(student)
        return JsonResponse({"success": True})

    return JsonResponse({"detail": "Method not allowed."}, status=405)


@require_POST
def api_admin_students_clear(request):
    user = _resolve_api_user(request)
    if not user or not user.is_superuser:
        return JsonResponse({"detail": "Unauthorized"}, status=401)

    student_ids = list(Student.objects.values_list("id", flat=True))
    users_to_delete = list(
        User.objects.filter(student__id__in=student_ids, is_staff=False, is_superuser=False).values_list("id", flat=True)
    )
    deleted_students = Student.objects.filter(id__in=student_ids).count()
    Student.objects.filter(id__in=student_ids).delete()
    if users_to_delete:
        User.objects.filter(id__in=users_to_delete).delete()

    return JsonResponse({"success": True, "deleted_students": deleted_students})


def api_admin_faculty(request):
    user = _resolve_api_user(request)
    if not user or not user.is_superuser:
        return JsonResponse({"detail": "Unauthorized"}, status=401)

    if request.method == "GET":
        faculty = User.objects.filter(is_staff=True, is_superuser=False).order_by("username")
        return JsonResponse({"faculty": [_serialize_faculty(item) for item in faculty]})

    if request.method == "POST":
        payload = json.loads(request.body.decode("utf-8"))
        try:
            values = _parse_faculty_payload(payload)
        except ValueError as exc:
            return JsonResponse({"detail": str(exc)}, status=400)

        created_user = User.objects.create_user(
            username=values["username"],
            password=values["password"],
            first_name=values["first_name"],
            last_name=values["last_name"],
            email=values["email"],
            is_staff=True,
            is_active=values["is_active"],
        )
        return JsonResponse(_serialize_faculty(created_user), status=201)

    return JsonResponse({"detail": "Method not allowed."}, status=405)


def api_admin_faculty_detail(request, pk):
    user = _resolve_api_user(request)
    if not user or not user.is_superuser:
        return JsonResponse({"detail": "Unauthorized"}, status=401)

    faculty_user = get_object_or_404(User, pk=pk, is_staff=True, is_superuser=False)

    if request.method == "GET":
        return JsonResponse(_serialize_faculty(faculty_user))

    if request.method in ["PUT", "PATCH"]:
        payload = json.loads(request.body.decode("utf-8"))
        try:
            values = _parse_faculty_payload(payload, existing_user=faculty_user)
        except ValueError as exc:
            return JsonResponse({"detail": str(exc)}, status=400)

        faculty_user.username = values["username"]
        faculty_user.first_name = values["first_name"]
        faculty_user.last_name = values["last_name"]
        faculty_user.email = values["email"]
        faculty_user.is_active = values["is_active"]
        if values["password"]:
            faculty_user.set_password(values["password"])
        faculty_user.save()
        return JsonResponse(_serialize_faculty(faculty_user))

    if request.method == "DELETE":
        faculty_user.delete()
        return JsonResponse({"success": True})

    return JsonResponse({"detail": "Method not allowed."}, status=405)


@require_POST
def api_student_enroll_face(request):
    user = _resolve_api_user(request)
    if not user:
        return JsonResponse({"detail": "Unauthorized"}, status=401)

    student = Student.objects.select_related("department").filter(user=user).first()
    if not student:
        return JsonResponse({"detail": "No student profile is linked to this account."}, status=404)

    payload = json.loads(request.body.decode("utf-8"))
    image_data = payload.get("image_data") or payload.get("face_image")
    section = (payload.get("section") or "").strip()

    if not image_data:
        return JsonResponse({"detail": "Missing image data."}, status=400)

    if "," in image_data:
        image_data = image_data.split(",", 1)[1]

    try:
        raw_bytes = base64.b64decode(image_data)
    except Exception:
        return JsonResponse({"detail": "Invalid image data."}, status=400)

    student.face_encoding = None
    student.face_image.save(
        f"face_{student.roll_number}.jpg",
        ContentFile(raw_bytes),
        save=False,
    )

    if section:
        student.section = section

    try:
        student.save()
    except Exception as ex:
        detail = "Could not generate the face profile."
        if settings.DEBUG:
            detail = f"{detail} {ex}"
        return JsonResponse({"detail": detail}, status=400)

    return JsonResponse(
        {
            "success": True,
            "student": _student_payload(student, request=request),
        }
    )


def api_public_stats(request):
    today = timezone.localdate()
    active_sessions = AttendanceSession.objects.filter(is_active=True).count()
    total_students = Student.objects.count()
    total_faculty = User.objects.filter(is_staff=True, is_superuser=False).count()
    attendance_today = AttendanceRecord.objects.filter(date=today).count()
    departments = Department.objects.count()

    return JsonResponse(
        {
            "active_sessions": active_sessions,
            "total_students": total_students,
            "total_faculty": total_faculty,
            "attendance_today": attendance_today,
            "departments": departments,
        }
    )


def api_professor_sessions(request):
    user = _resolve_api_user(request)
    if not user or not user.is_staff:
        return JsonResponse({"detail": "Unauthorized"}, status=401)

    sessions = AttendanceSession.objects.filter(is_active=True, created_by=user).order_by("-start_time")
    payload = [_session_payload(request, session) for session in sessions]

    return JsonResponse(
        {
            "sessions": payload,
            "user": {"username": user.username, "is_staff": True, "is_superuser": user.is_superuser},
        }
    )


@require_POST
def api_professor_sessions_create(request):
    user = _resolve_api_user(request)
    if not user or not user.is_staff:
        return JsonResponse({"detail": "Unauthorized"}, status=401)

    payload = json.loads(request.body.decode("utf-8"))
    department_name = (payload.get("department_name") or payload.get("department") or "").strip()
    section = (payload.get("section") or "").strip()
    subject = (payload.get("subject") or "").strip() or section or "General"
    semester = (payload.get("semester") or "").strip()
    attendance_date = payload.get("attendance_date")
    attendance_time = payload.get("attendance_time")

    if not section:
        return JsonResponse({"detail": "Section is required."}, status=400)

    parsed_date = None
    if attendance_date:
        try:
            parsed_date = datetime.strptime(attendance_date, "%Y-%m-%d").date()
        except ValueError:
            return JsonResponse({"detail": "Attendance date must be in YYYY-MM-DD format."}, status=400)

    parsed_time = None
    if attendance_time:
        try:
            parsed_time = datetime.strptime(attendance_time, "%H:%M").time()
        except ValueError:
            return JsonResponse({"detail": "Attendance time must be in HH:MM format."}, status=400)

    try:
        session = AttendanceSession.objects.create(
            subject=subject,
            department_name=department_name,
            section=section,
            semester=semester,
            attendance_date=parsed_date,
            attendance_time=parsed_time,
            created_by=user,
        )
    except Exception as ex:
        return JsonResponse({"detail": str(ex)}, status=400)

    return JsonResponse(
        _session_payload(request, session),
        status=201,
    )


@require_POST
def api_professor_session_deactivate(request, pk):
    user = _resolve_api_user(request)
    if not user or not user.is_staff:
        return JsonResponse({"detail": "Unauthorized"}, status=401)

    session = get_object_or_404(AttendanceSession, pk=pk, created_by=user)
    session.is_active = False
    session.save(update_fields=["is_active"])
    return JsonResponse({"success": True, "summary": _session_payload(request, session)})


def api_admin_timetable(request):
    user = _resolve_api_user(request)
    if not user or not user.is_superuser:
        return JsonResponse({"detail": "Unauthorized"}, status=401)

    if request.method == "GET":
        entries = TimetableEntry.objects.select_related("faculty", "department").order_by(
            "faculty__username", "day_of_week", "start_time"
        )
        payload = sorted(
            [_serialize_timetable_entry(entry) for entry in entries],
            key=lambda item: (item["faculty_username"].lower(), DAY_SEQUENCE.get(item["day_of_week"], 99), item["start_time"]),
        )
        return JsonResponse({"entries": payload, **_timetable_options_payload()})

    if request.method == "POST":
        payload = json.loads(request.body.decode("utf-8"))
        try:
            values = _parse_timetable_payload(payload)
        except ValueError as exc:
            return JsonResponse({"detail": str(exc)}, status=400)

        entry = TimetableEntry.objects.create(**values)
        return JsonResponse(_serialize_timetable_entry(entry), status=201)

    return JsonResponse({"detail": "Method not allowed."}, status=405)


def api_admin_timetable_detail(request, pk):
    user = _resolve_api_user(request)
    if not user or not user.is_superuser:
        return JsonResponse({"detail": "Unauthorized"}, status=401)

    entry = get_object_or_404(TimetableEntry.objects.select_related("faculty", "department"), pk=pk)

    if request.method == "GET":
        return JsonResponse(_serialize_timetable_entry(entry))

    if request.method in ["PUT", "PATCH"]:
        payload = json.loads(request.body.decode("utf-8"))
        try:
            values = _parse_timetable_payload(payload)
        except ValueError as exc:
            return JsonResponse({"detail": str(exc)}, status=400)

        for field, value in values.items():
            setattr(entry, field, value)
        entry.save()
        return JsonResponse(_serialize_timetable_entry(entry))

    if request.method == "DELETE":
        entry.delete()
        return JsonResponse({"success": True})

    return JsonResponse({"detail": "Method not allowed."}, status=405)


@require_POST
def api_admin_timetable_upload(request):
    user = _resolve_api_user(request)
    if not user or not user.is_superuser:
        return JsonResponse({"detail": "Unauthorized"}, status=401)

    payload = json.loads(request.body.decode("utf-8"))
    csv_text = payload.get("csv_text") or ""
    if not csv_text.strip():
        return JsonResponse({"detail": "CSV content is required."}, status=400)

    created = []
    reader = csv.DictReader(StringIO(csv_text.strip()))
    for row_number, row in enumerate(reader, start=2):
        normalized = {
            "faculty_id": row.get("faculty_id") or row.get("faculty") or row.get("faculty_username"),
            "program": row.get("program") or row.get("course") or "",
            "department_id": row.get("department_id") or "",
            "semester": row.get("semester") or "",
            "section": row.get("section") or "",
            "subject": row.get("subject") or "",
            "day_of_week": row.get("day_of_week") or row.get("day") or "",
            "start_time": row.get("start_time") or "",
            "end_time": row.get("end_time") or "",
            "is_active": str(row.get("is_active") or "true").strip().lower() not in ["false", "0", "no"],
        }

        faculty_value = str(normalized["faculty_id"]).strip()
        if faculty_value and not faculty_value.isdigit():
            matched_user = User.objects.filter(username=faculty_value, is_staff=True).first()
            normalized["faculty_id"] = matched_user.id if matched_user else faculty_value

        department_value = str(normalized["department_id"]).strip()
        if department_value and not department_value.isdigit():
            matched_department = Department.objects.filter(name__iexact=department_value).first()
            normalized["department_id"] = matched_department.id if matched_department else ""

        try:
            values = _parse_timetable_payload(normalized)
        except ValueError as exc:
            return JsonResponse({"detail": f"Row {row_number}: {exc}"}, status=400)

        created.append(TimetableEntry.objects.create(**values))

    return JsonResponse({"created": len(created), "entries": [_serialize_timetable_entry(entry) for entry in created]})


def api_session_detail(request, code):
    try:
        session = AttendanceSession.objects.get(session_code=code, is_active=True)
    except AttendanceSession.DoesNotExist:
        return JsonResponse({"detail": "Session not found or inactive."}, status=404)

    if _session_expired(session):
        session.is_active = False
        session.save(update_fields=["is_active"])
        return JsonResponse({"detail": "Session expired."}, status=404)

    payload = _session_payload(request, session)
    payload["expires_in_seconds"] = max(int((session.start_time + timedelta(hours=2) - timezone.now()).total_seconds()), 0)
    return JsonResponse(payload)


@require_POST
def api_session_mark(request, code):
    user = _resolve_api_user(request)
    if not user:
        return JsonResponse({"detail": "Unauthorized"}, status=401)

    try:
        session = AttendanceSession.objects.get(session_code=code, is_active=True)
    except AttendanceSession.DoesNotExist:
        return JsonResponse({"success": False, "message": "Session not found or inactive."}, status=404)

    if _session_expired(session):
        session.is_active = False
        session.save(update_fields=["is_active"])
        return JsonResponse({"success": False, "message": "Session has expired."}, status=400)

    payload = json.loads(request.body.decode("utf-8"))
    image_data = payload.get("image_data") or payload.get("face_image")
    qr_payload = payload.get("qr_payload") or payload.get("roll")

    try:
        student = Student.objects.get(user=user)
    except Student.DoesNotExist:
        return JsonResponse({"success": False, "message": "No student profile is linked to this account."}, status=404)

    if not _student_can_access_session(student, session):
        session_target = []
        student_profile = []
        if session.department_name:
            session_target.append(f"department {session.department_name}")
        if session.section:
            session_target.append(f"section {session.section}")
        if student.department:
            student_profile.append(f"department {student.department.name}")
        if student.section:
            student_profile.append(f"section {student.section}")

        detail = "Student not authorized for this session."
        if session_target and student_profile:
            detail = f"Student not authorized for this session. This session is for {' and '.join(session_target)}, but your profile is {' and '.join(student_profile)}."

        return JsonResponse({"success": False, "message": detail}, status=403)

    if AttendanceRecord.objects.filter(student=student, session=session).exists():
        return JsonResponse({"success": False, "message": "Already marked attendance for this session."}, status=400)

    if not qr_payload:
        return JsonResponse({"success": False, "message": "QR verification is required."}, status=400)

    qr_verified = _qr_payload_matches_student(student, qr_payload)
    if not qr_verified:
        return JsonResponse({"success": False, "message": "QR verification failed."}, status=400)

    face_verified = False
    if student.face_encoding and image_data:
        face_ok, _face_error = _verify_face_match(student, image_data)
        face_verified = bool(face_ok)

    verification_method = AttendanceRecord.METHOD_QR_FACE if face_verified else AttendanceRecord.METHOD_QR

    AttendanceRecord.objects.create(
        student=student,
        session=session,
        subject=None,
        date=timezone.localdate(),
        time=timezone.localtime().time(),
        status=AttendanceRecord.STATUS_PRESENT,
        method=verification_method,
    )

    message = "Attendance marked with QR verification."
    if face_verified:
        message = "Attendance marked with QR and face verification."

    return JsonResponse({"success": True, "message": message, "verification_method": verification_method})


def register_partial(request):
    if not request.user.is_staff:
        return render(
            request,
            "attendance/register_access_denied_fragment.html",
            {},
        )

    role = request.GET.get("role", "student")
    if role == "faculty":
        form = FacultyRegistrationForm()
        return render(request, "attendance/faculty_register_fragment.html", {"form": form})

    # Default to student registration fragment
    ensure_default_departments()
    form = StudentRegistrationForm()
    return render(request, "attendance/student_register_fragment.html", {"form": form})


def student_logout(request):
    logout(request)
    return redirect("attendance:student_login")


@login_required
def student_dashboard(request):
    try:
        student = Student.objects.get(user=request.user)
    except Student.DoesNotExist:
        messages.error(request, "No student profile found for this account.")
        return redirect("attendance:student_login")

    records = AttendanceRecord.objects.filter(student=student).select_related("subject")
    return render(
        request,
        "attendance/student_dashboard.html",
        {"student": student, "records": records},
    )


@login_required
def student_qr(request):
    try:
        student = Student.objects.get(user=request.user)
    except Student.DoesNotExist:
        messages.error(request, "No student profile found for this account.")
        return redirect("attendance:student_login")

    # Allow regenerating the QR code if requested (e.g., file missing or outdated)
    if request.GET.get("regen"):
        if student.qr_code:
            student.qr_code.delete(save=False)
        student._generate_qr()
        student.save(update_fields=["qr_code"])
        messages.success(request, "QR code regenerated successfully.")

    if not student.qr_code:
        student._generate_qr()
        student.save(update_fields=["qr_code"])

    return render(request, "attendance/student_qr.html", {"student": student})


def mark_attendance_manual(request, student_id):
    student = get_object_or_404(Student, pk=student_id)
    record, created = AttendanceRecord.objects.get_or_create(
        student=student,
        date=timezone.localdate(),
        defaults={
            "time": timezone.localtime().time(),
            "status": AttendanceRecord.STATUS_PRESENT,
            "method": AttendanceRecord.METHOD_MANUAL,
        },
    )
    if not created:
        record.time = timezone.localtime().time()
        record.status = AttendanceRecord.STATUS_PRESENT
        record.method = AttendanceRecord.METHOD_MANUAL
        record.save()

    messages.success(request, f"Marked {student.name} as present.")
    return redirect("attendance:student_list")


def _mark_attendance(student, method):
    return _mark_attendance_with_subject(student, method, None, None)


def _mark_attendance_with_subject(student, method, subject, session_time):
    record, created = AttendanceRecord.objects.get_or_create(
        student=student,
        date=timezone.localdate(),
        subject=subject,
        time=session_time or timezone.localtime().time(),
        defaults={
            "time": session_time or timezone.localtime().time(),
            "status": AttendanceRecord.STATUS_PRESENT,
            "method": method,
        },
    )
    if not created:
        record.time = session_time or timezone.localtime().time()
        record.method = method
        record.status = AttendanceRecord.STATUS_PRESENT
        record.save()
    return record


def scan_qr_attendance(request):
    result = None
    error = None
    scanned_roll = None
    subjects = Subject.objects.select_related("department").order_by("department__name", "name")
    selected_subject_id = None
    session_time = None

    if request.method == "POST":
        selected_subject_id = request.POST.get("subject")
        session_time_raw = request.POST.get("session_time")
        if session_time_raw:
            try:
                session_time = datetime.strptime(session_time_raw, "%H:%M").time()
            except ValueError:
                session_time = None
        try:
            import cv2
            from pyzbar.pyzbar import decode
        except ImportError:
            error = "Required libraries not installed (opencv-python, pyzbar)."
        else:
            cap = cv2.VideoCapture(0)
            start = time.time()
            scanned_student = None
            while time.time() - start < 25:
                ret, frame = cap.read()
                if not ret:
                    continue
                decoded = decode(frame)
                for obj in decoded:
                    scanned_roll = obj.data.decode("utf-8")
                    try:
                        scanned_student = Student.objects.get(roll_number=scanned_roll)
                        break
                    except Student.DoesNotExist:
                        scanned_student = None
                if scanned_student:
                    break
            cap.release()

            if scanned_student:
                subject = None
                if selected_subject_id:
                    subject = Subject.objects.filter(id=selected_subject_id).first()

                if can_mark_attendance(scanned_student, subject=subject):
                    _mark_attendance_with_subject(
                        scanned_student,
                        AttendanceRecord.METHOD_QR,
                        subject,
                        session_time,
                    )
                    result = f"Marked {scanned_student.name} present (QR scan)."
                else:
                    last = get_last_attendance_time(scanned_student, subject=subject)
                    wait_minutes = 20
                    error = (
                        f"Already marked within the last {wait_minutes} minutes. "
                        f"Last recorded at {last.astimezone(timezone.get_current_timezone()):%H:%M}."
                    )
        
    return render(
        request,
        "attendance/qr_scan.html",
        {
            "result": result,
            "error": error,
            "scanned_roll": scanned_roll,
            "subjects": subjects,
            "selected_subject_id": selected_subject_id,
        },
    )


@require_POST
def scan_qr_frame(request):
    try:
        payload = json.loads(request.body.decode("utf-8"))
        image_data = payload.get("image")
        subject_id = payload.get("subject_id")
        session_time_raw = payload.get("session_time")
        if not image_data:
            return JsonResponse({"matched": False, "message": "No image data received."}, status=400)

        if "," in image_data:
            image_data = image_data.split(",", 1)[1]

        raw_bytes = base64.b64decode(image_data)
    except Exception:
        return JsonResponse({"matched": False, "message": "Invalid image payload."}, status=400)

    try:
        import cv2
        import numpy as np
        from pyzbar.pyzbar import decode
    except ImportError as exc:
        return JsonResponse(
            {"matched": False, "message": f"Required library not installed: {exc}."},
            status=500,
        )

    try:
        image_array = np.frombuffer(raw_bytes, dtype=np.uint8)
        frame = cv2.imdecode(image_array, cv2.IMREAD_COLOR)
    except Exception:
        return JsonResponse({"matched": False, "message": "Could not decode image."}, status=400)

    decoded = decode(frame)
    if not decoded:
        return JsonResponse({"matched": False, "message": "No QR code detected."})

    try:
        scanned_roll = decoded[0].data.decode("utf-8")
        scanned_student = Student.objects.get(roll_number=scanned_roll)
    except Exception:
        return JsonResponse({"matched": False, "message": "QR code not recognized."})

    subject = None
    if subject_id:
        subject = Subject.objects.filter(id=subject_id).first()

    session_time = None
    if session_time_raw:
        try:
            session_time = datetime.strptime(session_time_raw, "%H:%M").time()
        except ValueError:
            session_time = None

    if not can_mark_attendance(scanned_student, subject=subject):
        last = get_last_attendance_time(scanned_student, subject=subject)
        wait_minutes = 20
        return JsonResponse(
            {
                "matched": False,
                "message": (
                    f"Already marked within the last {wait_minutes} minutes. "
                    f"Last recorded at {last.astimezone(timezone.get_current_timezone()):%H:%M}."
                ),
            }
        )

    record = _mark_attendance_with_subject(
        scanned_student,
        AttendanceRecord.METHOD_QR,
        subject,
        session_time,
    )

    timestamp = record.time.strftime("%H:%M:%S") if record.time else ""
    record_date = record.date.strftime("%Y-%m-%d") if record.date else ""

    return JsonResponse(
        {
            "matched": True,
            "message": f"Marked {scanned_student.name} present (QR scan).",
            "student": scanned_student.name,
            "time": timestamp,
            "date": record_date,
        }
    )


def scan_face_attendance(request):
    subjects = Subject.objects.select_related("department").order_by("department__name", "name")
    return render(request, "attendance/face_scan.html", {"subjects": subjects})


@require_POST
def scan_face_frame(request):
    try:
        payload = json.loads(request.body.decode("utf-8"))
        image_data = payload.get("image")
        subject_id = payload.get("subject_id")
        session_time_raw = payload.get("session_time")
        if not image_data:
            return JsonResponse({"matched": False, "message": "No image data received."}, status=400)

        if "," in image_data:
            image_data = image_data.split(",", 1)[1]

        raw_bytes = base64.b64decode(image_data)
    except Exception:
        return JsonResponse({"matched": False, "message": "Invalid image payload."}, status=400)

    try:
        import os
        import tempfile

        import cv2
        import numpy as np

        # Force DeepFace to use legacy tf-keras for compatibility.
        os.environ.setdefault("TF_USE_LEGACY_KERAS", "1")
        from deepface import DeepFace
    except ImportError as exc:
        return JsonResponse(
            {"matched": False, "message": f"Required library not installed: {exc}."},
            status=500,
        )

    try:
        image_array = np.frombuffer(raw_bytes, dtype=np.uint8)
        frame = cv2.imdecode(image_array, cv2.IMREAD_COLOR)
    except Exception:
        return JsonResponse({"matched": False, "message": "Could not decode image."}, status=400)

    students_with_encodings = (
        Student.objects.exclude(face_encoding__isnull=True)
        .exclude(face_encoding__exact="")
    )
    known_embeddings = []
    student_map = []
    for student in students_with_encodings:
        try:
            known_embeddings.append(np.array(student.face_encoding))
            student_map.append(student)
        except Exception:
            continue

    if not known_embeddings:
        return JsonResponse(
            {"matched": False, "message": "No student face encodings are available."},
            status=400,
        )

    session_time = None
    if session_time_raw:
        try:
            session_time = datetime.strptime(session_time_raw, "%H:%M").time()
        except ValueError:
            session_time = None

    # Save frame to a temp file so DeepFace can read it
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
        tmp_path = tmp.name
    cv2.imwrite(tmp_path, frame)

    try:
        live_result = DeepFace.represent(
            img_path=tmp_path,
            model_name="Facenet",
            enforce_detection=False,
        )
    except Exception:
        live_result = []
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass

    if not live_result:
        return JsonResponse({"matched": False, "message": "No face detected."})

    live_embedding = np.array(live_result[0]["embedding"])

    # Cosine similarity comparison
    best_score = -1.0
    best_idx = -1
    for idx, known in enumerate(known_embeddings):
        dot = np.dot(live_embedding, known)
        norm = np.linalg.norm(live_embedding) * np.linalg.norm(known)
        score = dot / norm if norm > 0 else 0.0
        if score > best_score:
            best_score = score
            best_idx = idx

    if best_score > 0.85 and best_idx >= 0:
        matched_student = student_map[best_idx]
        subject = None
        if subject_id:
            subject = Subject.objects.filter(id=subject_id).first()
        _mark_attendance_with_subject(matched_student, AttendanceRecord.METHOD_FACE, subject, session_time)
        return JsonResponse(
            {
                "matched": True,
                "message": f"Marked {matched_student.name} present (Face recognition).",
                "student": matched_student.name,
                "confidence": round(best_score * 100, 2),
            }
        )

    return JsonResponse(
        {"matched": False, "message": "No matching face detected.", "confidence": round(best_score * 100, 2)}
    )


@require_POST
def rebuild_face_encodings(request):
    students = Student.objects.exclude(face_image__isnull=True).exclude(face_image="")
    updated = 0
    for student in students:
        student.face_encoding = None
        student.save()
        updated += 1
    messages.success(request, f"Rebuilt face encodings for {updated} students.")
    return redirect("attendance:dashboard")


def attendance_history(request):
    departments = Department.objects.all().order_by("name")
    subjects = Subject.objects.select_related("department").order_by("department__name", "name")

    records = AttendanceRecord.objects.select_related("student", "student__department", "subject")

    department_id = request.GET.get("department")
    subject_id = request.GET.get("subject")
    date_filter = request.GET.get("date")
    student_roll = request.GET.get("roll")

    if department_id:
        records = records.filter(student__department_id=department_id)
    if subject_id:
        records = records.filter(subject_id=subject_id)
    if date_filter:
        records = records.filter(date=date_filter)
    if student_roll:
        records = records.filter(student__roll_number__icontains=student_roll)

    return render(
        request,
        "attendance/attendance_history.html",
        {
            "records": records,
            "departments": departments,
            "subjects": subjects,
            "selected_department": department_id,
            "selected_subject": subject_id,
            "selected_date": date_filter,
            "student_roll": student_roll,
        },
    )


def export_attendance_csv(request):
    import csv

    records = AttendanceRecord.objects.select_related("student", "student__department", "subject")

    department_id = request.GET.get("department")
    subject_id = request.GET.get("subject")
    date_filter = request.GET.get("date")
    student_roll = request.GET.get("roll")

    if department_id:
        records = records.filter(student__department_id=department_id)
    if subject_id:
        records = records.filter(subject_id=subject_id)
    if date_filter:
        records = records.filter(date=date_filter)
    if student_roll:
        records = records.filter(student__roll_number__icontains=student_roll)

    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="attendance.csv"'

    writer = csv.writer(response)
    writer.writerow(["Student", "Roll", "Department", "Subject", "Date", "Day", "Time", "Status", "Method"])
    for record in records:
        writer.writerow(
            [
                record.student.name,
                record.student.roll_number,
                record.student.department.name if record.student.department else "",
                record.subject.name if record.subject else "",
                record.date,
                record.day_of_week,
                record.time,
                record.get_status_display(),
                record.get_method_display(),
            ]
        )

    return response


def export_attendance_pdf(request):
    from io import BytesIO

    from reportlab.lib.pagesizes import letter
    from reportlab.lib.units import inch
    from reportlab.pdfgen import canvas

    records = AttendanceRecord.objects.select_related("student", "student__department", "subject")

    department_id = request.GET.get("department")
    subject_id = request.GET.get("subject")
    date_filter = request.GET.get("date")
    student_roll = request.GET.get("roll")

    if department_id:
        records = records.filter(student__department_id=department_id)
    if subject_id:
        records = records.filter(subject_id=subject_id)
    if date_filter:
        records = records.filter(date=date_filter)
    if student_roll:
        records = records.filter(student__roll_number__icontains=student_roll)

    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=letter)
    width, height = letter

    pdf.setFont("Helvetica-Bold", 14)
    pdf.drawString(1 * inch, height - 1 * inch, "Attendance History")

    pdf.setFont("Helvetica", 9)
    y = height - 1.4 * inch
    line_height = 0.22 * inch

    headers = ["Student", "Roll", "Department", "Subject", "Date", "Day", "Time", "Status", "Method"]
    pdf.drawString(1 * inch, y, " | ".join(headers))
    y -= line_height

    for record in records:
        row = [
            record.student.name,
            record.student.roll_number,
            record.student.department.name if record.student.department else "",
            record.subject.name if record.subject else "",
            str(record.date),
            record.day_of_week,
            str(record.time),
            record.get_status_display(),
            record.get_method_display(),
        ]
        pdf.drawString(1 * inch, y, " | ".join(row))
        y -= line_height
        if y < 1 * inch:
            pdf.showPage()
            y = height - 1 * inch

    pdf.save()
    buffer.seek(0)

    response = HttpResponse(buffer.read(), content_type="application/pdf")
    response["Content-Disposition"] = 'attachment; filename="attendance.pdf"'
    return response
