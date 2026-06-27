from django.db import models
from django.contrib.auth.models import User


class UserProfile(models.Model):
    """Extended user profile with role-based access."""
    ROLE_CHOICES = [
        ('admin', 'Admin'),
        ('tester', 'Tester'),
        ('viewer', 'Viewer'),
    ]

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='api_profile')
    role = models.CharField(max_length=10, choices=ROLE_CHOICES, default='tester')
    is_active_profile = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user.username} ({self.get_role_display()})"


class Environment(models.Model):
    """Test environment configuration."""
    name = models.CharField(max_length=100)
    base_url = models.CharField(max_length=500)
    description = models.TextField(blank=True)
    auth_type = models.CharField(
        max_length=20,
        choices=[
            ('none', 'None'),
            ('basic', 'Basic Auth'),
            ('digest', 'Digest Auth'),
            ('bearer', 'Bearer Token'),
            ('oauth2', 'OAuth 2.0'),
            ('api_key', 'API Key'),
            ('oauth1', 'OAuth 1.0'),
            ('awsv4', 'AWS Signature'),
            ('ntlm', 'NTLM Auth'),
        ],
        default='none'
    )
    auth_credentials = models.TextField(blank=True, help_text='JSON-formatted credentials')
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='environments')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return f"{self.name} ({self.base_url})"


class Project(models.Model):
    """Project folder for organizing test cases."""
    name = models.CharField(max_length=100, unique=True)
    order = models.IntegerField(default=0)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='projects')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['order', 'name']

    def __str__(self):
        return self.name


class TestCase(models.Model):
    """API test case definition."""
    HTTP_METHOD_CHOICES = [
        ('GET', 'GET'),
        ('POST', 'POST'),
        ('PUT', 'PUT'),
        ('PATCH', 'PATCH'),
        ('DELETE', 'DELETE'),
    ]

    AUTH_TYPE_CHOICES = [
        ('none', 'None'),
        ('inherit', 'Inherit from Environment'),
        ('basic', 'Basic Auth'),
        ('bearer', 'Bearer Token'),
        ('api_key', 'API Key'),
    ]

    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    module = models.CharField(max_length=100, blank=True, help_text='Module or API group')
    project = models.CharField(max_length=100, blank=True, help_text='Project name')
    endpoint = models.CharField(max_length=1000, help_text='API endpoint path (relative to base URL)')
    http_method = models.CharField(max_length=7, choices=HTTP_METHOD_CHOICES, default='GET')
    headers = models.TextField(blank=True, default='{}', help_text='JSON-formatted headers')
    query_params = models.TextField(blank=True, default='{}', help_text='JSON-formatted query parameters')
    path_params = models.TextField(blank=True, default='{}', help_text='JSON-formatted path parameters')
    request_body = models.TextField(blank=True, help_text='Request body (JSON or raw text)')
    form_data = models.TextField(blank=True, default='[]', help_text='JSON array of form-data rows [{type, key, value, description}]')
    auth_type = models.CharField(max_length=10, choices=AUTH_TYPE_CHOICES, default='inherit')
    auth_credentials = models.TextField(blank=True, help_text='JSON-formatted auth credentials')
    expected_status_code = models.IntegerField(null=True, blank=True)
    expected_response_content = models.TextField(blank=True, help_text='Expected content in response body')
    expected_response_time_ms = models.IntegerField(null=True, blank=True, help_text='Max acceptable response time in ms')
    is_active = models.BooleanField(default=True)
    order = models.IntegerField(default=0)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='test_cases')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['order', 'name']

    def __str__(self):
        return f"[{self.http_method}] {self.name}"


class TestExecution(models.Model):
    """Record of a test case execution."""
    RESULT_CHOICES = [
        ('passed', 'Passed'),
        ('failed', 'Failed'),
        ('error', 'Error'),
    ]

    test_case = models.ForeignKey(TestCase, on_delete=models.CASCADE, related_name='executions')
    environment = models.ForeignKey(Environment, on_delete=models.SET_NULL, null=True, related_name='executions')
    executed_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='executions')
    executed_at = models.DateTimeField(auto_now_add=True)
    result_status = models.CharField(max_length=10, choices=RESULT_CHOICES)
    request_url = models.TextField(blank=True)
    request_headers = models.TextField(blank=True)
    request_body = models.TextField(blank=True)
    response_status_code = models.IntegerField(null=True)
    response_headers = models.TextField(blank=True)
    response_body = models.TextField(blank=True)
    response_time_ms = models.IntegerField(null=True)
    status_code_match = models.BooleanField(null=True)
    content_match = models.BooleanField(null=True)
    time_within_threshold = models.BooleanField(null=True)
    error_message = models.TextField(blank=True)

    class Meta:
        ordering = ['-executed_at']

    def __str__(self):
        return f"{self.test_case.name} - {self.result_status} ({self.executed_at})"


class ApiModule(models.Model):
    """Uploaded API module from Postman/OpenAPI collection."""
    MODULE_AUTH_CHOICES = [
        ('none', 'None'),
        ('oauth2', 'OAuth 2.0'),
    ]
    OAUTH2_ADD_TO_CHOICES = [
        ('request_headers', 'Request Headers'),
        ('request_url', 'Request URL'),
    ]

    name = models.CharField(max_length=255)
    base_path = models.CharField(max_length=1000, blank=True, help_text='Base URL path for this API module')
    description = models.TextField(blank=True)
    source_file = models.CharField(max_length=255, blank=True, help_text='Original uploaded filename')
    module_auth_type = models.CharField(max_length=20, choices=MODULE_AUTH_CHOICES, default='none')
    oauth2_add_to = models.CharField(max_length=20, choices=OAUTH2_ADD_TO_CHOICES, default='request_headers')
    oauth2_client_id = models.CharField(max_length=255, blank=True)
    oauth2_client_secret = models.CharField(max_length=255, blank=True)
    oauth2_token_url = models.CharField(max_length=1000, blank=True)
    oauth2_current_token = models.TextField(blank=True)
    oauth2_header_prefix = models.CharField(max_length=50, default='Bearer')
    oauth2_token_updated_at = models.DateTimeField(null=True, blank=True)
    uploaded_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='api_modules')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class ModuleEndpoint(models.Model):
    """Endpoint parsed from an uploaded API collection."""
    module = models.ForeignKey(ApiModule, on_delete=models.CASCADE, related_name='endpoints')
    name = models.CharField(max_length=255, help_text='Request/operation name')
    http_method = models.CharField(max_length=7, default='GET')
    endpoint_path = models.CharField(max_length=1000, help_text='API endpoint path')
    headers = models.TextField(blank=True, default='{}')
    request_body = models.TextField(blank=True)
    default_payload = models.TextField(blank=True, help_text='Default payload JSON for this endpoint')
    expected_responses = models.TextField(blank=True, default='{}', help_text='JSON map of status_code -> sample response body')
    description = models.TextField(blank=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return f"[{self.http_method}] {self.name}"


class AuditLog(models.Model):
    """Audit trail for user actions."""
    ACTION_CHOICES = [
        ('login', 'User Login'),
        ('logout', 'User Logout'),
        ('testcase_create', 'Test Case Created'),
        ('testcase_update', 'Test Case Updated'),
        ('testcase_delete', 'Test Case Deleted'),
        ('testcase_execute', 'Test Case Executed'),
        ('environment_create', 'Environment Created'),
        ('environment_update', 'Environment Updated'),
        ('environment_delete', 'Environment Deleted'),
        ('user_create', 'User Created'),
        ('user_update', 'User Updated'),
        ('user_deactivate', 'User Deactivated'),
    ]

    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='api_audit_logs')
    action = models.CharField(max_length=30, choices=ACTION_CHOICES)
    target_type = models.CharField(max_length=50, blank=True)
    target_id = models.IntegerField(null=True, blank=True)
    details = models.TextField(blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-timestamp']

    def __str__(self):
        return f"{self.user} - {self.get_action_display()} ({self.timestamp})"


class ThemeSettings(models.Model):
    """Global UI theme settings for the application."""

    THEME_MODE_CHOICES = [
        ('light', 'Light'),
        ('dark', 'Dark'),
    ]

    name = models.CharField(max_length=50, default='default', unique=True)
    theme_mode = models.CharField(max_length=10, choices=THEME_MODE_CHOICES, default='light')
    primary_color = models.CharField(max_length=7, default='#0f766e')
    accent_color = models.CharField(max_length=7, default='#c0841f')
    background_color = models.CharField(max_length=7, default='#eef3f7')
    surface_color = models.CharField(max_length=7, default='#ffffff')
    sidebar_start_color = models.CharField(max_length=7, default='#0f172a')
    sidebar_end_color = models.CharField(max_length=7, default='#1e293b')
    text_color = models.CharField(max_length=7, default='#0f172a')
    border_color = models.CharField(max_length=7, default='#dbe3ec')
    updated_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='updated_themes')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return f"Theme: {self.name} ({self.theme_mode})"
