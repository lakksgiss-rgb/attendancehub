# AttendanceHub Project Analysis

## 1. Executive Summary

AttendanceHub is a role-based attendance management system built on Django. It supports three operational roles:

- Admin: manages timetable, students, faculty, and system-wide monitoring
- Faculty: creates and closes live attendance sessions and monitors student check-ins
- Student: enrolls face data, displays QR identity, joins a session, and marks attendance through QR plus face verification

The student attendance flow now supports QR-only check-ins; face verification is optional when the camera is not available or the user prefers QR-only. Face verification is still performed when a live camera frame is available for higher assurance.

The faculty dashboard has been refactored with separate session cards and a scrollable student board to improve usability with large class sizes.

The project originally used a Django backend with a React frontend. That React application has been replaced by a Django-rendered UI using Django templates, HTMX for role/login interactivity, and targeted browser-side JavaScript for camera and QR scanning on the student session page.

> NOTE: Some sections later in this document reference legacy React/SPA components. The current active application is driven by Django templates and the files under `attendance/templates/attendance/htmx/`.

Current runtime model:

- Django owns the backend, rendered UI, routing, authentication, media, and API layer
- SQLite is the active database in development
- DeepFace plus TensorFlow handle face embedding generation and face verification
- QR generation and QR verification remain part of the student identity and attendance flow
- The live UI is served directly from Django routes such as `/`, `/login`, `/dashboard`, `/student`, `/admin-dashboard`, and `/session/<code>`

This document reflects the current post-React architecture as it exists now.

---

## 2. Current Technology Stack

### 2.1 Core Backend

- Python 3.10
- Django 5.2.12
- SQLite3
- django-cors-headers
- Standard Django auth, session, CSRF, static, and media subsystems

### 2.2 UI Layer

- Django templates
- HTMX 1.9.x loaded from CDN
- Custom CSS stored in Django static files at `attendance/static/attendance/css/ui.css`
- Small focused inline JavaScript only where browser hardware access is required

### 2.3 Identity and Verification

- DeepFace 0.0.79
- TensorFlow 2.20.x
- tf_keras compatibility package
- OpenCV
- Facenet embeddings
- qrcode for QR generation
- Browser `BarcodeDetector` when supported for QR scanning in the session page

### 2.4 Reporting and Utility Libraries

- reportlab for PDF export
- pandas for CSV/data workflows
- pillow for image handling

### 2.5 Deployment/Runtime Notes

- Django currently serves on port `8000`
- Media is served in development via Django when `DEBUG=True`
- ngrok can still be used to expose the Django app publicly
- The old Vite/React runtime is no longer part of the active application path

---

## 3. Current Project Structure

```text
amsdjango/
├── ams/
│   ├── manage.py
│   ├── db.sqlite3
│   ├── media/
│   ├── ams/
│   │   ├── __init__.py
│   │   ├── asgi.py
│   │   ├── settings.py
│   │   ├── urls.py
│   │   └── wsgi.py
│   └── attendance/
│       ├── __init__.py
│       ├── admin.py
│       ├── apps.py
│       ├── forms.py
│       ├── models.py
│       ├── tests.py
│       ├── urls.py
│       ├── utils.py
│       ├── views.py
│       ├── migrations/
│       ├── static/
│       │   └── attendance/
│       │       └── css/
│       │           └── ui.css
│       └── templates/
│           └── attendance/
│               ├── htmx/
│               └── legacy template files
├── requirements.txt
└── PROJECT_ANALYSIS.md
```

Important note:

- The application no longer depends on a live frontend source tree for runtime behavior
- The active UI is now inside Django templates under `attendance/templates/attendance/htmx/`

---

## 4. Architectural Overview

### 4.1 Architectural Style

The current system is a server-rendered Django application with a hybrid interaction model:

- Server-rendered HTML for page structure and most user flows
- HTMX for role-switching and login panel updates
- Direct Django POST handling for admin, faculty, and student form actions
- Client-side JavaScript only for camera capture, QR scanning, and live image submission where browser APIs are necessary

This is not a pure SPA and not a pure zero-JavaScript app. It is a pragmatic hybrid where Django is the owner of application state and browser JavaScript is used only at the edges where hardware interaction is required.

### 4.2 Why This Architecture Fits the Domain

Attendance systems need strict server-side control for:

- identity verification
- duplicate attendance prevention
- role enforcement
- session window enforcement
- student eligibility checks
- auditability of attendance records

The current architecture keeps those concerns in Django while still allowing a modern UI and device-friendly workflows.

### 4.3 Current System Boundaries

Owned by Django:

- page routing
- role-based login routing
- student, faculty, admin dashboards
- session rendering
- API endpoints
- QR image generation
- face image storage and embedding generation
- face match verification
- attendance record creation
- timetable CRUD
- student CRUD
- faculty CRUD

Owned by the browser:

- camera access for face enrollment
- camera access on live attendance session page
- QR detection from live frames or uploaded images
- previewing captured images before attendance submit

---

## 5. Runtime Entry Points and Route Ownership

### 5.1 Live UI Routes

These are the active user-facing routes now served directly by Django:

- `/` → home page
- `/login` and `/login/` → role-based login page
- `/dashboard` and `/dashboard/` → faculty dashboard
- `/student` and `/student/` → student dashboard
- `/admin-dashboard` and `/admin-dashboard/` → admin dashboard
- `/session/<code>` and `/session/<code>/` → student attendance session page

### 5.2 HTMX/Preview Aliases

These routes still exist as aliases and migration-era compatibility routes:

- `/ui/`
- `/ui/login/`
- `/ui/admin/`
- `/ui/faculty/`
- `/ui/student/`
- `/ui/session/<code>/`

They map to the same Django-rendered view layer and are no longer the primary user entry points.

### 5.3 Login Interaction Routes

- `/login/panel/` → returns the role-specific login fragment
- `/login/submit/` → handles HTMX login submit and returns `HX-Redirect` on success

### 5.4 Django Admin Routing

Defined in `ams/urls.py`:

- `/admin/` and `/admin/<path>` redirect to `/login?role=admin`
- raw Django admin remains available under `/django-admin/`

This means the default `/admin/` URL is intentionally replaced with the custom admin login flow.

### 5.5 API Routes

The application still exposes JSON APIs for current UI logic and future reuse.

Key endpoints include:

- `/api/login/`
- `/api/logout/`
- `/api/csrf/`
- `/api/user/`
- `/api/public/stats/`
- `/api/student/qr-image/`
- `/api/student/enroll-face/`
- `/api/dashboard/faculty/overview/`
- `/api/dashboard/student/overview/`
- `/api/dashboard/admin/overview/`
- `/api/professor/sessions/`
- `/api/professor/sessions/create/`
- `/api/professor/sessions/<pk>/deactivate/`
- `/api/admin/timetable/`
- `/api/admin/students/`
- `/api/admin/faculty/`
- `/api/sessions/<code>/`
- `/api/sessions/<code>/mark/`

These remain valuable because the current session page uses the attendance API directly from browser JavaScript.

---

## 6. Core Domain Model

### 6.1 Django User

The built-in `User` model is the base authentication identity.

Role interpretation:

- `is_superuser=True` → Admin
- `is_staff=True` and not superuser → Faculty
- linked `Student` profile and not staff → Student

This design avoids a custom auth model while still supporting role separation.

### 6.2 Faculty Proxy Model

`Faculty` is a proxy model over Django `User`.

Purpose:

- cleaner Django admin management for faculty accounts
- user management without introducing a separate faculty table

### 6.3 Department

Fields:

- `name`

Purpose:

- groups students
- supports timetable filtering
- supports admin reporting
- contributes to eligibility checks for sessions

### 6.4 Subject

Fields:

- `name`
- `department`

Current role:

- still part of the schema
- used more by legacy/older flows
- newer timetable/session flows usually store subject names directly in `TimetableEntry` and `AttendanceSession`

### 6.5 Student

Fields:

- `user`
- `name`
- `roll_number`
- `department`
- `section`
- `qr_mode`
- `face_image`
- `face_encoding`
- `qr_code`

Business role:

- represents the academic identity of a student
- links login identity to attendance identity
- stores the student’s QR and face verification state

Important behavior:

- can generate permanent QR codes
- can generate signed daily QR payloads
- generates face embeddings when a face image exists and no embedding is present
- stores QR code image content in media for permanent QR mode

### 6.6 TimetableEntry

Fields:

- `faculty`
- `program`
- `department`
- `semester`
- `section`
- `subject`
- `day_of_week`
- `start_time`
- `end_time`
- `is_active`

Business role:

- drives faculty “current class” and “next class” context
- powers admin timetable management
- provides defaults for faculty session creation

### 6.7 AttendanceSession

Fields:

- `subject`
- `department_name`
- `section`
- `semester`
- `attendance_date`
- `attendance_time`
- `start_time`
- `is_active`
- `session_code`
- `created_by`

Business role:

- represents a live attendance room
- is the unit students join to mark attendance
- is the unit faculty manage and close

Important model behavior:

- auto-fills date and time when omitted
- auto-generates a unique 12-character uppercase session code
- deactivates any existing active session for the same section when saving an active session
- enforces one active session per section through a conditional unique constraint

### 6.8 AttendanceRecord

Fields:

- `student`
- `session`
- `subject`
- `date`
- `day_of_week`
- `time`
- `status`
- `method`
- `created_at`

Supported methods:

- `qr`
- `face`
- `manual`
- `qr_face`

Current live method:

- `qr_face` for the main attendance experience

Business role:

- immutable-style audit record of attendance activity
- source for reporting, dashboards, and history

---

## 7. Data Integrity and Access Rules

### 7.1 Session Eligibility

Student access to a session is determined by `_student_can_access_session()` in `views.py`.

The logic compares:

- session section
- student section
- session department name
- student department name

Important nuance:

- legacy student records may have the department name stored in `section`
- the eligibility logic explicitly compensates for that older data pattern

### 7.2 One Active Session Per Section

When an `AttendanceSession` is saved as active:

- any other active session for the same section is deactivated
- the database also enforces a conditional uniqueness constraint for active sessions per section

This reduces duplicate live sessions and avoids ambiguous session selection for students.

### 7.3 Duplicate Attendance Protection

The main live session attendance endpoint prevents duplicates by rejecting attendance if:

- an `AttendanceRecord` already exists for the same `student` and `session`

There is also a utility-level 20-minute rule for older attendance flows in `utils.py`, but the live session flow primarily relies on session-based duplicate prevention.

---

## 8. Authentication and Authorization

### 8.1 Primary Auth Model

The application uses Django session authentication for the live rendered UI.

### 8.2 API Auth Support

The system still supports signed bearer-style tokens through `_issue_api_token()` and `_resolve_api_user()`.

This was previously useful for SPA behavior and still works for API access patterns.

### 8.3 Role Enforcement Rules

Admin-only checks:

- must be authenticated
- must be `is_superuser=True`

Faculty-only checks:

- must be authenticated
- must be `is_staff=True`
- must not be superuser for the faculty dashboard path

Student-only checks:

- must be authenticated
- must not be staff
- must have a linked `Student` profile for core student operations

### 8.4 Redirect Behavior

Role-based redirect behavior is centralized in the login flow:

- admin login → `/admin-dashboard`
- faculty login → `/dashboard`
- student login → `/student`

Protected route unauthenticated redirects:

- faculty pages → `/login?role=professor`
- student pages → `/login?role=student`
- admin pages → `/login?role=admin`

---

## 9. UI Module Breakdown

### 9.1 Home Page

Template:

- `attendance/templates/attendance/htmx/home.html`

Responsibilities:

- presents role entry options
- shows public metrics from `_ui_shell_context()`
- loads role-specific login panels via HTMX

### 9.2 Login Page

Templates:

- `attendance/templates/attendance/htmx/login.html`
- `attendance/templates/attendance/htmx/_login_panel.html`

Responsibilities:

- role-aware login messaging
- admin/faculty/student switching without full reload using HTMX
- submits login using HTMX and receives redirect behavior through `HX-Redirect`

### 9.3 Admin Dashboard

Template:

- `attendance/templates/attendance/htmx/admin_dashboard.html`

View logic:

- `ui_admin()`
- `_ui_admin_context()`

Responsibilities:

- system statistics
- department activity summary
- recent attendance events
- recently closed sessions
- timetable creation and delete operations
- student create/delete/clear operations
- faculty create/delete operations
- faculty timetable grouping for schedule board display

### 9.4 Faculty Dashboard

Template:

- `attendance/templates/attendance/htmx/faculty_dashboard.html`

View logic:

- `ui_faculty()`
- `_ui_faculty_context()`
- `_faculty_overview_payload()`

Responsibilities:

- current class summary
- next class summary
- create attendance session
- apply class presets from timetable
- monitor active sessions
- show present/absent/total/rate
- show recent check-ins
- show student tiles that turn green for present and rose for absent
- close live session and generate summary

### 9.5 Student Dashboard

Template:

- `attendance/templates/attendance/htmx/student_dashboard.html`

View logic:

- `ui_student()`
- `_ui_student_context()`
- `_student_overview_payload()`

Responsibilities:

- attendance statistics
- subject-wise attendance breakdown
- recent attendance history
- QR display
- face enrollment workflow
- session code or link entry

### 9.6 Student Session Page

Template:

- `attendance/templates/attendance/htmx/student_session.html`

View logic:

- `ui_session()`
- `_resolve_ui_session_state()`

Responsibilities:

- session info display
- camera preview
- QR scanning from live camera frame
- QR scanning from uploaded image
- candidate face capture preview
- attendance submit to live API endpoint
- success/error feedback to student

Important note:

- this page intentionally includes inline JavaScript because camera access, image capture, QR scanning, and asynchronous attendance submit cannot be realistically handled by server rendering alone

---

## 10. Business Workflows by Role

### 10.1 Admin Workflow

1. Admin signs in through `/login?role=admin`
2. Admin lands on `/admin-dashboard`
3. Admin can create or delete timetable entries
4. Admin can create or delete students and faculty users
5. Admin can clear all students if needed
6. Admin reviews department metrics, recent attendance, and recently closed sessions

Admin value:

- centralized operational control
- data maintenance
- cross-role visibility

### 10.2 Faculty Workflow

1. Faculty signs in through `/login?role=professor`
2. Faculty lands on `/dashboard`
3. Faculty views current and next classes based on timetable
4. Faculty creates a session manually or using current/next class defaults
5. Students join using code/link
6. Faculty watches live roster tiles turn present as attendance is marked
7. Faculty closes the session
8. Closed-session summary becomes visible in admin dashboard

Faculty value:

- live monitoring
- low-friction session creation
- schedule-aware workflow

### 10.3 Student Workflow

1. Student signs in through `/login?role=student`
2. Student lands on `/student`
3. Student enrolls face profile if not already available
4. Student views QR identity
5. Student enters session code or pastes session link
6. Student lands on `/session/<code>`
7. Student camera opens
8. QR is scanned live or uploaded
9. Candidate face image is captured
10. Browser posts image and QR payload to `/api/sessions/<code>/mark/`
11. Backend verifies eligibility, duplicate state, QR, and face match
12. Attendance record is created on success

Student value:

- self-service attendance
- clear identity confirmation
- mobile-friendly session flow

---

## 11. Attendance Verification Pipeline

### 11.1 Face Enrollment

Enrollment occurs from the student dashboard.

Flow:

1. browser opens camera
2. browser captures a frame
3. image data is posted to the server
4. server stores `face_image`
5. `Student.save()` triggers embedding generation if needed
6. DeepFace creates a Facenet embedding
7. embedding is stored in `face_encoding`

### 11.2 QR Identity Handling

Student QR modes:

- permanent QR: usually plain roll number
- daily QR: signed payload containing roll number, date, and mode

Supporting helpers:

- `_student_qr_payload()`
- `_active_student_qr_payload()`
- `_qr_payload_matches_student()`
- `_qr_image_bytes()`

### 11.3 Live Session Verification

The live attendance endpoint is `/api/sessions/<code>/mark/`.

Server-side steps:

1. resolve authenticated user
2. resolve active session
3. reject expired session
4. resolve linked student profile
5. reject ineligible student for section/department mismatch
6. reject duplicate attendance for same student/session
7. verify QR payload when present
8. verify live face against stored embedding using `_verify_face_match()`
9. create `AttendanceRecord`

### 11.4 Face Match Logic

`_verify_face_match()`:

- decodes base64 image
- uses OpenCV to write temp image
- sets `TF_USE_LEGACY_KERAS=1`
- imports DeepFace dynamically
- generates a live embedding using Facenet
- compares the live embedding to stored embedding using cosine similarity
- accepts if similarity is at least `0.85`

This threshold is hard-coded in the current implementation.

### 11.5 Session Expiry

`_session_expired()` currently expires a session after two hours based on `start_time`.

Implication:

- active sessions are not indefinite
- expired sessions are rejected at API level even if still marked active

---

## 12. Reporting and Dashboard Payloads

### 12.1 Admin Overview Payload

`_admin_overview_payload()` returns:

- students count
- faculty count
- departments count
- active sessions count
- active timetable entries count
- attendance events for today
- department summary
- recent attendance
- recent closed sessions

### 12.2 Faculty Overview Payload

`_faculty_overview_payload()` returns:

- current class
- next class
- today schedule
- active sessions
- faculty stats

### 12.3 Student Overview Payload

`_student_overview_payload()` returns:

- total classes seen by eligibility rules
- present count
- absent count
- attendance percentage
- subject summary
- attendance history

These payload builders are important because they represent the main composition layer between models and UI.

---

## 13. Legacy Components Still Present

The codebase still contains legacy or fallback template flows.

Examples:

- classic student registration templates
- classic professor dashboard template flows
- classic session invalid and already-marked templates
- CSV/PDF export templates and utilities
- older scan pages and history pages

This means the application is now Django-only, but not all parts of the repository follow the same UI style or architecture depth yet.

These legacy modules are not dead code automatically; some are still reachable and useful for fallback/admin workflows.

---

## 14. Static Files, Media, and Assets

### 14.1 Static UI Styling

The active UI stylesheet is:

- `attendance/static/attendance/css/ui.css`

This file contains the visual system used by the current dashboards and session pages.

### 14.2 Media Storage

Media is stored under `ams/media/` in development.

Typical media content:

- student face images
- generated QR code images

### 14.3 Template Layout Ownership

The base layout is:

- `attendance/templates/attendance/htmx/base_shell.html`

It loads:

- Django static CSS
- HTMX from CDN

---

## 15. Security and Operational Considerations

### 15.1 Strengths

- server-side role enforcement
- session-based authentication for live UI
- explicit student eligibility checks
- duplicate attendance protection
- session auto-deactivation by section
- face verification performed server-side

### 15.2 Risks and Gaps

- SQLite is not ideal for concurrent production usage
- camera and QR flows depend on browser support and permissions
- the attendance session page still uses inline JavaScript and would benefit from modularization
- there is no substantial automated test suite
- some legacy routes and new routes coexist, which increases maintenance complexity
- DeepFace/TensorFlow dependencies are heavy and can be fragile across environments

### 15.3 Current Testing Reality

Current automated test surface is minimal:

- `attendance/tests.py` is effectively empty
- there are no active Python integration tests for the critical attendance flows

Practical validation used so far has mainly been:

- `manage.py check`
- route smoke tests
- manual render verification

---

## 16. Performance and Maintainability Notes

### 16.1 Positive Changes from the Current Architecture

- one framework owns both UI and backend behavior
- deployment complexity is lower than a split Django + React application
- no separate frontend build is required for runtime operation
- route ownership is simpler and easier to debug

### 16.2 Areas That Need Refactoring Later

- `attendance/views.py` is very large and mixes UI views, API views, serialization, validation, and helper logic
- the session page JavaScript is embedded inside a template and should eventually be extracted to static JS
- admin/faculty/student view helpers could be separated into dedicated service modules
- some older template routes could be retired or reorganized

---

## 17. Recommended Near-Term Roadmap

### 17.1 High Priority

- add automated tests for:
   - role login redirects
   - student enrollment
   - session eligibility
   - duplicate attendance rejection
   - admin CRUD actions
   - faculty session create/close
- modularize `attendance/views.py`
- move session-page inline JavaScript into static files

### 17.2 Medium Priority

- migrate from SQLite to PostgreSQL for stronger multi-user reliability
- add audit logging for admin operations
- add stronger validation around section and department normalization
- improve error reporting for camera and QR failures

### 17.3 Feature Opportunities

- attendance analytics by subject/faculty/department over longer windows
- downloadable admin/faculty session summaries
- stronger mobile UX tuning for low-end devices
- student profile self-service beyond face enrollment
- timetable import validation preview before commit

### 17.4 Security/Operational Improvements

- environment-based secret and debug configuration
- production static/media strategy
- stronger per-action permissions if roles expand
- optional liveness checks or anti-spoofing for face capture

---

## 18. Final Assessment

The project is now a coherent Django-first attendance platform with:

- live role-based dashboards
- timetable-aware faculty operations
- admin management capabilities
- student QR and face-based attendance marking
- direct Django routing without a React dependency

The most important architectural win is simplification:

- one framework now owns the UI and backend
- the user-facing routes are live and validated
- the attendance domain rules remain server-controlled

The most important remaining engineering weakness is test coverage and concentration of logic inside `attendance/views.py`.

If those two areas are addressed next, the project will be in a much stronger position for long-term maintenance and deployment.
- shows verification identity card state

### 7.8 `frontend/src/pages/AdminDashboard.jsx`
Current admin operations panel.

Responsibilities:
- shows top-level platform stats
- manages timetable entries
- uploads timetable CSVs
- creates, edits, deletes, and clears students
- creates, edits, deletes faculty
- shows department activity
- shows recent attendance events
- shows recently closed session summaries
- auto-refreshes periodically
- compresses long sections with separate show-more controls

### 7.9 `frontend/src/components/dashboard/*`
Reusable dashboard UI components.

Responsibilities:
- section panels
- stat cards
- consistent dashboard visual language

### 7.10 `frontend/src/index.css`
Global styling system.

Responsibilities:
- dark theme
- glass panels
- metric cards
- buttons
- hero effects
- modern visual style across dashboards

---

## 8. System Workflows

## 8.1 Authentication workflow

1. User opens React login page
2. React requests CSRF token if needed
3. Credentials are posted to `/api/login/`
4. Django authenticates using built-in auth
5. Backend returns role and profile information
6. Frontend redirects to:
   - admin dashboard
   - faculty dashboard
   - student dashboard

## 8.2 Admin workflow

Admin is the central operator of the system.

### Admin can do
- manage timetable entries manually
- upload timetable in bulk through CSV
- create student accounts
- edit student details at any time
- choose student QR mode
- delete one student or clear all students
- create faculty accounts
- edit faculty details at any time
- remove faculty accounts
- monitor department counts and activity
- review recent attendance events
- review recently closed session summaries

### Admin systematic value
Admin prepares the academic structure before live attendance happens. The admin module is not just a user table; it is the control center that defines who exists in the system and what timetable context faculty sees.

## 8.3 Faculty workflow

Faculty is responsible for live attendance operation.

### Faculty can do
- log into the faculty workspace
- see current class and next class from timetable
- load timetable defaults into the session creation form
- create an attendance session for a department, section, semester, subject, date, and time
- get a session code and join link
- monitor present and absent counts live
- see recent check-ins
- see each student tile as present or absent
- close a session when attendance is complete

### Faculty systematic value
Faculty converts timetable structure into live attendance windows. It is the operational layer between academic schedule data and actual attendance collection.

## 8.4 Student workflow

Student is the attendance participant.

### Student can do
- log into the student portal
- review attendance summary and history data
- enroll face image if not already enrolled
- view QR code image
- enter a session code or use a session link
- open the live session page
- scan or upload their QR code
- allow camera access for face verification
- submit attendance for that active session

### Student systematic value
Student does not create attendance. Student verifies identity and completes attendance in a controlled live window.

## 8.5 Session attendance workflow

This is the most important live workflow in the project.

1. Faculty creates a session
2. Backend creates an `AttendanceSession`
3. Backend generates a join path and join URL
4. Student opens the session page
5. Session page loads session detail from `/api/sessions/<code>/`
6. Student page loads signed-in student data from `/api/user/`
7. Student QR is checked
8. Live camera frame is captured
9. Backend checks that:
   - session exists and is active
   - student belongs to the allowed department and section
   - attendance is not already marked for that session
   - QR payload belongs to the current student
   - face embedding matches the stored student embedding
10. Backend creates an `AttendanceRecord`
11. Faculty dashboard shows the student as present
12. Faculty closes session when done
13. Admin dashboard later shows the closed session summary

---

## 9. Verification Logic

## 9.1 QR verification
The system supports two QR modes.

### Permanent QR
- payload is basically the roll number
- useful for ID-card style fixed QR

### Daily QR
- payload is signed and date-bound
- includes roll number and current date
- useful when admin wants a rotating QR model

### Matching logic
The backend accepts either:
- the plain student roll number
- a signed payload that resolves to the correct student and valid date

This is important because earlier project versions mixed plain and signed QR patterns.

## 9.2 Face verification
The system uses DeepFace with Facenet embeddings.

### Enrollment
- student face image is saved
- embedding is extracted and stored in `face_encoding`

### Session mark
- live frame is captured
- embedding is extracted for the live frame
- cosine similarity is computed against the saved embedding
- the threshold currently used is approximately 0.85

### Why this matters
This keeps the student attendance mark tied to a real camera-based face check rather than only to QR possession.

---

## 10. Access Control Rules

### 10.1 Role access
- Admin endpoints require superuser
- Faculty endpoints require staff user
- Student attendance marking requires logged-in linked student

### 10.2 Session eligibility
Student can mark attendance only if their profile matches the session constraints.

Matching dimensions:
- department
- section
- legacy section fallback logic where older data stored department in section

### 10.3 Duplicate prevention
Student cannot mark attendance again for the same session once already recorded.

---

## 11. Current Feature Inventory

### Core platform features
- Django template-driven UI
- role-based login
- custom admin workspace
- faculty dashboard
- student dashboard
- unified server-rendered application (no separate frontend build)

### Attendance features
- live attendance session creation
- one active session per section rule
- session code and join link
- QR plus face verification
- duplicate attendance prevention
- active and closed session tracking

### Student features
- face enrollment
- QR image access
- student summary dashboard
- session code entry
- session link support

### Faculty features
- current class context from timetable
- next class preview
- session preset loading
- active session board
- student present and absent tiles
- recent check-ins
- session closing with summary

### Admin features
- timetable create, edit, delete
- timetable CSV upload
- student create, edit, delete, clear
- faculty create, edit, delete
- department summaries
- recent activity summary
- closed session summary cards
- auto-refresh admin board
- compact show-more buttons for long sections

### Support and compatibility features
- CSRF-safe frontend calls
- HTMX and API endpoints for partial UI updates
- static assets served from Django static files
- ngrok-safe origin support
- same-origin or split-origin dev support

---

## 12. Legacy vs Modern Parts

The project currently contains a modern Django template/HTMX UI and a few legacy template routes that remain for compatibility.

### Primary flows (current UI)
- Home
- Login
- Faculty dashboard
- Student dashboard
- Admin dashboard
- Session page
- main API-driven workflows

### Legacy or secondary flows still present
- old student register template
- old faculty register template
- old dashboard template
- legacy QR scan and face scan template routes
- history export pages and PDF/CSV routes
- some classic attendance/session template routes

### What this means
The system is primarily a Django-rendered application. Legacy pages exist as fallbacks and administrative utilities, but the main user paths are through the current template/HTMX UI.

---

## 13. Current Strengths

### Product strengths
- clear separation of admin, faculty, and student responsibilities
- real session-based attendance instead of generic one-click marking
- live faculty visibility into session attendance
- direct admin control over users and timetable
- multiple access methods across desktop, phone, LAN, and ngrok

### Technical strengths
- backend remains source of truth for all sensitive validation
- face and QR logic are modular enough to evolve
- student QR mode supports future security changes
- timetable system now feeds actual role workflows instead of existing as static data
- UI is served directly through Django templates and HTMX (legacy React routes have been removed)

---

## 14. Current Limitations

### Data and workflow limitations
- no dedicated attendance analytics page yet
- no approval workflow for timetable publishing
- no role for department coordinators or HOD users
- no audit log for admin edits

### Technical limitations
- SQLite is fine for development but not ideal for multi-user production use
- face verification is CPU-heavy and may slow down under larger load
- barcode support depends partly on browser capabilities for client-side scanning
- some legacy pages and APIs overlap conceptually with the newer React flows
- exports are still mostly tied to legacy Django pages

### UX limitations
- admin dashboard still contains many responsibilities in one page
- no tabbed layout yet for admin workspace
- limited search and filtering in big data lists
- no faculty-specific historical attendance analytics panel yet

---

## 15. Recommended Future Features

The next features should be chosen in layers.

### 15.1 High-priority product improvements
- admin dashboard tabs for Timetable, Students, Faculty, Reports, Sessions
- search and filters for students, faculty, and timetable entries
- faculty attendance history by subject, section, and date range
- student attendance trend charts by subject and semester
- downloadable session summary from the admin and faculty boards

### 15.2 Attendance workflow improvements
- attendance late threshold and auto-mark late state
- faculty override for absent-to-present correction
- session pause and resume instead of only create and close
- attendance reason codes for exceptional manual changes
- multi-photo face enrollment for stronger match quality

### 15.3 Security and reliability improvements
- signed QR everywhere for stricter consistency
- QR expiry countdown and clear invalid-state messaging
- audit logs for admin edits and deletions
- role-specific permission groups instead of only staff and superuser
- backup and restore tooling for timetable and attendance data

### 15.4 Platform and deployment improvements
- PostgreSQL migration for production readiness
- object storage for media files
- Redis-based caching or background tasks if load increases
- asynchronous face processing if session traffic grows
- Docker and deployment manifests

### 15.5 Academic operations improvements
- multiple sections per faculty slot
- holiday and special-day timetable overrides
- semester calendar integration
- attendance shortage warnings
- department-wise reports and advisor views

### 15.6 Student experience improvements
- session QR scanner directly inside student dashboard before opening session
- attendance notifications after successful marking
- downloadable student ID QR card
- per-subject attendance deficit warnings
- profile self-update for allowed fields

---

## 16. Suggested Next Development Roadmap

### Phase 1
- add admin tabs
- add search and filtering
- add faculty history and reports

### Phase 2
- improve QR consistency and logging
- add attendance correction workflow
- add export from React dashboards

### Phase 3
- migrate to PostgreSQL
- add production deployment structure
- improve scaling of face verification

---

## 17. Final System Understanding

The updated project works as a structured attendance platform with the following systematic chain:

1. Admin defines operational data and users
2. Timetable gives academic context
3. Faculty creates and supervises live sessions
4. Student verifies identity using QR plus face
5. Backend validates and records attendance
6. Faculty sees live present or absent status
7. Admin sees overall activity and closed session summaries

In practical terms:
- Admin controls the platform
- Faculty controls the live classroom session
- Student completes the attendance proof
- Django secures the logic
- Django templates + HTMX deliver the day-to-day experience

This is now more than a simple attendance app. It is a role-based academic attendance operations system with live verification, session control, timetable context, and centralized administration.

---

## 18. IBM Cloud Deployment

The project can be deployed to IBM Cloud using either **Cloud Foundry (Python Buildpack)** or **Code Engine (container build)**. The repository includes support files to make either approach straightforward.

### 18.1 Recommended preparation

1. Ensure production settings are applied:
   - `DEBUG=False`
   - `ALLOWED_HOSTS` contains the app domain or `*` for early testing.
   - Configure secret keys and database URLs via environment variables.
2. Use PostgreSQL (or another managed database) instead of SQLite for production.
3. Use object storage (IBM Cloud Object Storage) for media if you expect more than local filesystem usage.

### 18.2 Cloud Foundry (Python buildpack)

Files included:
- `Procfile` (runs Gunicorn)
- `runtime.txt` (selects Python runtime)
- `manifest.yml` (app definition)

Deployment steps (after installing IBM Cloud CLI + Cloud Foundry plugin):

1. `ibmcloud login --sso` (or `ibmcloud login`)
2. `ibmcloud target --cf` (select target org/space)
3. `ibmcloud cf push` (uses `manifest.yml`)

### 18.3 IBM Cloud Code Engine (Container)

Files included:
- `Dockerfile` (builds a container image)

Deployment steps (after installing IBM Cloud CLI + Code Engine plugin):

1. `ibmcloud login --sso`
2. `ibmcloud target --cf` (or set correct region)
3. `ibmcloud ce project create --name my-project` (if needed)
4. `ibmcloud ce build create --name attendance-build --image <REGISTRY>/attendance:latest --strategy docker --source .`
5. `ibmcloud ce app create --name attendance-app --image <REGISTRY>/attendance:latest --cpu 0.5 --memory 1G --port 8080`

### 18.4 Notes for both deployment approaches

- The app listens on `$PORT` (Cloud Foundry/Code Engine set this automatically) and uses `gunicorn ams.wsgi`.
- Ensure environment variables are set for Django secrets, database URL, and allowed hosts.
- For debugging, use `ibmcloud cf logs <app-name> --recent` or `ibmcloud ce app logs --name <app-name> --tail`.
