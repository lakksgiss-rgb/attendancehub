from django import forms

from .models import AttendanceSession, Student


class StudentRegistrationForm(forms.ModelForm):
    live_photo_data = forms.CharField(
        required=False,
        widget=forms.HiddenInput(),
    )

    class Meta:
        model = Student
        fields = ["name", "roll_number", "department", "section", "qr_mode", "face_image"]
        widgets = {
            "name": forms.TextInput(
                attrs={
                    "class": "mt-1 block w-full rounded-md border border-slate-300 px-3 py-2 shadow-sm focus:border-blue-500 focus:outline-none focus:ring focus:ring-blue-200",
                }
            ),
            "roll_number": forms.TextInput(
                attrs={
                    "class": "mt-1 block w-full rounded-md border border-slate-300 px-3 py-2 shadow-sm focus:border-blue-500 focus:outline-none focus:ring focus:ring-blue-200",
                }
            ),
            "department": forms.Select(
                attrs={
                    "class": "mt-1 block w-full rounded-md border border-slate-300 bg-white px-3 py-2 shadow-sm focus:border-blue-500 focus:outline-none focus:ring focus:ring-blue-200",
                }
            ),
            "qr_mode": forms.Select(
                attrs={
                    "class": "mt-1 block w-full rounded-md border border-slate-300 bg-white px-3 py-2 shadow-sm focus:border-blue-500 focus:outline-none focus:ring focus:ring-blue-200",
                }
            ),
            "face_image": forms.FileInput(
                attrs={
                    "class": "mt-1 block w-full text-sm text-slate-900 file:mr-4 file:rounded-full file:border-0 file:bg-blue-50 file:px-4 file:py-2 file:text-sm file:font-semibold file:text-blue-700 hover:file:bg-blue-100",
                }
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["department"].empty_label = "Select Department"

    def clean(self):
        return super().clean()


class StudentEditForm(forms.ModelForm):
    live_photo_data = forms.CharField(
        required=False,
        widget=forms.HiddenInput(),
    )

    class Meta:
        model = Student
        fields = ["name", "roll_number", "department", "section", "qr_mode", "face_image"]
        widgets = {
            "name": forms.TextInput(
                attrs={
                    "class": "mt-1 block w-full rounded-md border border-slate-300 px-3 py-2 shadow-sm focus:border-blue-500 focus:outline-none focus:ring focus:ring-blue-200",
                }
            ),
            "roll_number": forms.TextInput(
                attrs={
                    "class": "mt-1 block w-full rounded-md border border-slate-300 px-3 py-2 shadow-sm focus:border-blue-500 focus:outline-none focus:ring focus:ring-blue-200",
                }
            ),
            "department": forms.Select(
                attrs={
                    "class": "mt-1 block w-full rounded-md border border-slate-300 bg-white px-3 py-2 shadow-sm focus:border-blue-500 focus:outline-none focus:ring focus:ring-blue-200",
                }
            ),
            "qr_mode": forms.Select(
                attrs={
                    "class": "mt-1 block w-full rounded-md border border-slate-300 bg-white px-3 py-2 shadow-sm focus:border-blue-500 focus:outline-none focus:ring focus:ring-blue-200",
                }
            ),
            "face_image": forms.FileInput(
                attrs={
                    "class": "mt-1 block w-full text-sm text-slate-900 file:mr-4 file:rounded-full file:border-0 file:bg-blue-50 file:px-4 file:py-2 file:text-sm file:font-semibold file:text-blue-700 hover:file:bg-blue-100",
                }
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["department"].empty_label = "Select Department"


class FacultyRegistrationForm(forms.Form):
    username = forms.CharField(
        max_length=150,
        widget=forms.TextInput(
            attrs={
                "class": "mt-1 block w-full rounded-md border border-slate-300 px-3 py-2 shadow-sm focus:border-blue-500 focus:outline-none focus:ring focus:ring-blue-200",
            }
        ),
    )
    password = forms.CharField(
        widget=forms.PasswordInput(
            attrs={
                "class": "mt-1 block w-full rounded-md border border-slate-300 px-3 py-2 shadow-sm focus:border-blue-500 focus:outline-none focus:ring focus:ring-blue-200",
            }
        ),
    )
    confirm_password = forms.CharField(
        widget=forms.PasswordInput(
            attrs={
                "class": "mt-1 block w-full rounded-md border border-slate-300 px-3 py-2 shadow-sm focus:border-blue-500 focus:outline-none focus:ring focus:ring-blue-200",
            }
        ),
    )

    def clean(self):
        cleaned = super().clean()
        password = cleaned.get("password")
        confirm = cleaned.get("confirm_password")
        if password and confirm and password != confirm:
            self.add_error("confirm_password", "Passwords do not match.")
        return cleaned


class AttendanceSessionForm(forms.ModelForm):
    class Meta:
        model = AttendanceSession
        fields = ["subject", "department_name", "section", "semester", "attendance_date", "attendance_time"]
        widgets = {
            "subject": forms.TextInput(
                attrs={
                    "class": "mt-1 block w-full rounded-md border border-slate-300 px-3 py-2 shadow-sm focus:border-blue-500 focus:outline-none focus:ring focus:ring-blue-200",
                    "placeholder": "e.g. Computer Science",
                }
            ),
            "department_name": forms.TextInput(
                attrs={
                    "class": "mt-1 block w-full rounded-md border border-slate-300 px-3 py-2 shadow-sm focus:border-blue-500 focus:outline-none focus:ring focus:ring-blue-200",
                    "placeholder": "e.g. MCA",
                }
            ),
            "section": forms.TextInput(
                attrs={
                    "class": "mt-1 block w-full rounded-md border border-slate-300 px-3 py-2 shadow-sm focus:border-blue-500 focus:outline-none focus:ring focus:ring-blue-200",
                    "placeholder": "e.g. A1",
                }
            ),
            "semester": forms.TextInput(
                attrs={
                    "class": "mt-1 block w-full rounded-md border border-slate-300 px-3 py-2 shadow-sm focus:border-blue-500 focus:outline-none focus:ring focus:ring-blue-200",
                    "placeholder": "e.g. 3rd Semester",
                }
            ),
            "attendance_date": forms.DateInput(
                attrs={
                    "type": "date",
                    "class": "mt-1 block w-full rounded-md border border-slate-300 px-3 py-2 shadow-sm focus:border-blue-500 focus:outline-none focus:ring focus:ring-blue-200",
                }
            ),
            "attendance_time": forms.TimeInput(
                attrs={
                    "type": "time",
                    "class": "mt-1 block w-full rounded-md border border-slate-300 px-3 py-2 shadow-sm focus:border-blue-500 focus:outline-none focus:ring focus:ring-blue-200",
                }
            ),
        }
