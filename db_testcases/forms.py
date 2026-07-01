from django import forms
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.models import Group, User

from .models import DatabaseConnection, ProjectFolder, TestCase, Theme, UserThemePreference


class DatabaseConnectionForm(forms.ModelForm):
    class Meta:
        model = DatabaseConnection
        fields = [
            "name",
            "db_type",
            "host",
            "port",
            "database_name",
            "service_name",
            "username",
            "password",
            "options_json",
        ]
        widgets = {
            "password": forms.PasswordInput(render_value=True),
            "options_json": forms.Textarea(attrs={"rows": 3}),
        }


class TestCaseForm(forms.ModelForm):
    project_folder = forms.ModelChoiceField(
        queryset=ProjectFolder.objects.all().order_by("name"),
        required=False,
        empty_label=None,
    )

    class Meta:
        model = TestCase
        fields = [
            "name",
            "project_folder",
            "connection",
            "test_type",
            "table_name",
            "query",
            "expected_value",
            "form_data",
            "comparison_operator",
            "is_active",
            "notes",
        ]
        widgets = {
            "query": forms.Textarea(attrs={"rows": 4}),
            "notes": forms.Textarea(attrs={"rows": 3}),
            "expected_value": forms.HiddenInput(),
            "form_data": forms.HiddenInput(),
        }

    def save(self, commit=True):
        instance = super().save(commit=False)
        if not instance.pk and (instance.sort_order or 0) == 0:
            sibling_qs = TestCase.objects.filter(project_folder=instance.project_folder).order_by("-sort_order")
            last = sibling_qs.first()
            instance.sort_order = (last.sort_order + 10) if last else 10
        if commit:
            instance.save()
            self.save_m2m()
        return instance

    def clean(self):
        cleaned_data = super().clean()
        test_type = cleaned_data.get("test_type")
        table_name = (cleaned_data.get("table_name") or "").strip()
        query = (cleaned_data.get("query") or "").strip()
        expected_value = (cleaned_data.get("expected_value") or "").strip()

        if test_type == TestCase.TestType.TABLE_EXISTS:
            if not table_name:
                self.add_error("table_name", "Table name is required for TABLE_EXISTS.")

        if test_type == TestCase.TestType.ROW_COUNT:
            if not table_name:
                self.add_error("table_name", "Table name is required for ROW_COUNT.")

        if test_type == TestCase.TestType.QUERY_VALUE:
            if not query:
                self.add_error("query", "Query is required for QUERY_VALUE.")

        return cleaned_data


class AdminUserForm(forms.ModelForm):
    groups = forms.ModelMultipleChoiceField(
        queryset=Group.objects.all().order_by("name"),
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )
    theme = forms.ModelChoiceField(queryset=Theme.objects.all().order_by("name"), required=False)
    new_password = forms.CharField(
        required=False,
        widget=forms.PasswordInput,
        help_text="Leave blank to keep the current password.",
    )
    confirm_password = forms.CharField(
        required=False,
        widget=forms.PasswordInput,
        help_text="Re-enter the new password.",
    )

    class Meta:
        model = User
        fields = ["username", "first_name", "last_name", "email", "is_active", "is_staff"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.fields["groups"].initial = self.instance.groups.all()
            pref = getattr(self.instance, "theme_preference", None)
            self.fields["theme"].initial = pref.theme if pref else None

    def clean(self):
        cleaned_data = super().clean()
        new_password = cleaned_data.get("new_password")
        confirm_password = cleaned_data.get("confirm_password")

        if new_password or confirm_password:
            if not new_password or not confirm_password:
                raise forms.ValidationError("Both password fields are required to change password.")
            if new_password != confirm_password:
                raise forms.ValidationError("The new password and confirmation do not match.")
            validate_password(new_password, self.instance)

        return cleaned_data

    def save(self, commit=True):
        user = super().save(commit=commit)
        new_password = self.cleaned_data.get("new_password")
        if new_password:
            user.set_password(new_password)
            user.save(update_fields=["password"])
        if user.pk:
            user.groups.set(self.cleaned_data["groups"])
            pref, _ = UserThemePreference.objects.get_or_create(user=user)
            pref.theme = self.cleaned_data["theme"]
            pref.save()
        return user


class ThemeForm(forms.ModelForm):
    class Meta:
        model = Theme
        fields = [
            "name",
            "primary_color",
            "accent_color",
            "background_color",
            "surface_color",
            "text_color",
            "border_color",
            "sidebar_start_color",
            "sidebar_end_color",
            "is_default",
        ]
        widgets = {
            "primary_color": forms.TextInput(attrs={"type": "color"}),
            "accent_color": forms.TextInput(attrs={"type": "color"}),
            "background_color": forms.TextInput(attrs={"type": "color"}),
            "surface_color": forms.TextInput(attrs={"type": "color"}),
            "text_color": forms.TextInput(attrs={"type": "color"}),
            "border_color": forms.TextInput(attrs={"type": "color"}),
            "sidebar_start_color": forms.TextInput(attrs={"type": "color"}),
            "sidebar_end_color": forms.TextInput(attrs={"type": "color"}),
        }


class ProjectFolderForm(forms.ModelForm):
    class Meta:
        model = ProjectFolder
        fields = ["name", "parent", "sort_order"]

    def clean_name(self):
        name = (self.cleaned_data.get("name") or "").strip()
        if not name:
            raise forms.ValidationError("Project name is required.")
        return name

    def clean(self):
        cleaned_data = super().clean()
        name = cleaned_data.get("name")
        parent = cleaned_data.get("parent")
        if not name:
            return cleaned_data

        dup_qs = ProjectFolder.objects.filter(parent=parent, name__iexact=name)
        if self.instance and self.instance.pk:
            dup_qs = dup_qs.exclude(pk=self.instance.pk)
        if dup_qs.exists():
            self.add_error("name", "A project with this name already exists.")

        return cleaned_data
