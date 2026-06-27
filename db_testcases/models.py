from django.db import models
from django.contrib.auth.models import User


class DatabaseConnection(models.Model):
    class DbType(models.TextChoices):
        ORACLE = "ORACLE", "Oracle"
        POSTGRES = "POSTGRES", "PostgreSQL"
        SQLSERVER = "SQLSERVER", "SQL Server"

    name = models.CharField(max_length=120, unique=True)
    db_type = models.CharField(max_length=20, choices=DbType.choices)
    host = models.CharField(max_length=255)
    port = models.PositiveIntegerField()
    database_name = models.CharField(max_length=255, blank=True)
    service_name = models.CharField(max_length=255, blank=True)
    username = models.CharField(max_length=255)
    password = models.CharField(max_length=255)
    options_json = models.TextField(blank=True, help_text="Optional JSON for extra driver options")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.name} ({self.db_type})"


class ProjectFolder(models.Model):
    name = models.CharField(max_length=150)
    parent = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        related_name="children",
        null=True,
        blank=True,
    )
    sort_order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["sort_order", "name"]
        unique_together = [("parent", "name")]

    def __str__(self):
        return self.name


class TestCase(models.Model):
    class TestType(models.TextChoices):
        CONNECTION = "CONNECTION", "DB Connection"
        TABLE_EXISTS = "TABLE_EXISTS", "Table Exists"
        ROW_COUNT = "ROW_COUNT", "Row Count"
        QUERY_VALUE = "QUERY_VALUE", "Query Value"

    class Operator(models.TextChoices):
        EQ = "=", "="
        GT = ">", ">"
        GTE = ">=", ">="
        LT = "<", "<"
        LTE = "<=", "<="
        CONTAINS = "contains", "contains"

    name = models.CharField(max_length=150, unique=True)
    project_folder = models.ForeignKey(
        ProjectFolder,
        on_delete=models.SET_NULL,
        related_name="test_cases",
        null=True,
        blank=True,
    )
    sort_order = models.PositiveIntegerField(default=0)
    connection = models.ForeignKey(DatabaseConnection, on_delete=models.CASCADE, related_name="test_cases")
    test_type = models.CharField(max_length=30, choices=TestType.choices)
    table_name = models.CharField(max_length=255, blank=True)
    query = models.TextField(blank=True)
    expected_value = models.CharField(max_length=255, blank=True)
    comparison_operator = models.CharField(max_length=20, choices=Operator.choices, default=Operator.EQ)
    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name


class TestExecution(models.Model):
    class Status(models.TextChoices):
        PASS = "PASS", "Pass"
        FAIL = "FAIL", "Fail"
        ERROR = "ERROR", "Error"

    test_case = models.ForeignKey(TestCase, on_delete=models.CASCADE, related_name="executions")
    status = models.CharField(max_length=10, choices=Status.choices)
    details = models.TextField(blank=True)
    actual_value = models.TextField(blank=True)
    executed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-executed_at"]

    def __str__(self):
        return f"{self.test_case.name} - {self.status}"


class Theme(models.Model):
    name = models.CharField(max_length=80, unique=True)
    primary_color = models.CharField(max_length=20, default="#0f766e")
    accent_color = models.CharField(max_length=20, default="#f59e0b")
    background_color = models.CharField(max_length=20, default="#f3f7f5")
    surface_color = models.CharField(max_length=20, default="#ffffff")
    text_color = models.CharField(max_length=20, default="#122322")
    border_color = models.CharField(max_length=20, default="#d7e2df")
    sidebar_start_color = models.CharField(max_length=20, default="#0b2d2a")
    sidebar_end_color = models.CharField(max_length=20, default="#133734")
    is_default = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class UserThemePreference(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="theme_preference")
    theme = models.ForeignKey(Theme, on_delete=models.SET_NULL, null=True, blank=True, related_name="users")

    def __str__(self):
        if self.theme:
            return f"{self.user.username} -> {self.theme.name}"
        return f"{self.user.username} -> default"


class UserProfile(models.Model):
    class Role(models.TextChoices):
        ADMIN = "Admin", "Admin"
        TESTER = "Tester", "Tester"
        VIEWER = "Viewer", "Viewer"

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="db_profile")
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.VIEWER)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user.username} ({self.role})"


class AuditLog(models.Model):
    class Action(models.TextChoices):
        CREATE = "CREATE", "Create"
        UPDATE = "UPDATE", "Update"
        DELETE = "DELETE", "Delete"
        EXECUTE = "EXECUTE", "Execute"
        LOGIN = "LOGIN", "Login"
        LOGOUT = "LOGOUT", "Logout"
        TEST_CONNECTION = "TEST_CONNECTION", "Test Connection"

    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name="db_audit_logs")
    action = models.CharField(max_length=30, choices=Action.choices)
    target_type = models.CharField(max_length=50, help_text="Model name (e.g., TestCase, DatabaseConnection)")
    target_id = models.PositiveIntegerField(null=True, blank=True)
    target_name = models.CharField(max_length=255, blank=True)
    details = models.TextField(blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "-created_at"]),
            models.Index(fields=["target_type", "-created_at"]),
        ]

    def __str__(self):
        return f"{self.action} {self.target_type} by {self.user.username} at {self.created_at}"
