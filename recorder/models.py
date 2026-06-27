import uuid
from django.db import models


# ---------------------------------------------------------------------------
# Multi-tenant models (managed by Django)
# ---------------------------------------------------------------------------

class Tenant(models.Model):
    """An isolated organisational unit.  Each user belongs to exactly one tenant."""
    id         = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name       = models.CharField(max_length=200, unique=True)
    slug       = models.SlugField(max_length=100, unique=True)
    is_active  = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "tenants"

    def __str__(self):
        return self.name


class UserProfile(models.Model):
    """Extends Django's built-in User with a tenant assignment."""
    user   = models.OneToOneField(
        "auth.User", on_delete=models.CASCADE, related_name="profile"
    )
    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="members",
    )

    class Meta:
        db_table = "user_profiles"

    def __str__(self):
        return f"{self.user.username} / tenant={self.tenant_id}"


class DataEntry(models.Model):
    record_id = models.UUIDField()
    step_no = models.IntegerField()
    field_name = models.TextField(null=True, blank=True)
    value = models.TextField(null=True, blank=True)
    engine = models.CharField(max_length=20, null=True, blank=True)
    created_at = models.DateTimeField()
    tenant_id = models.UUIDField(null=True, blank=True, db_index=True)

    class Meta:
        db_table = "data"
        managed = False
        ordering = ["step_no"]


class Locator(models.Model):
    record_id = models.UUIDField()
    step_no = models.IntegerField()
    strategy = models.TextField()
    locator = models.TextField()
    is_primary = models.BooleanField(default=False)
    locator_rank = models.IntegerField(null=True, blank=True)
    pos_x = models.FloatField(null=True, blank=True)
    pos_y = models.FloatField(null=True, blank=True)
    folder_name = models.TextField(null=True, blank=True)
    engine = models.CharField(max_length=20, null=True, blank=True)
    created_at = models.DateTimeField(null=True, blank=True)
    tenant_id = models.UUIDField(null=True, blank=True, db_index=True)

    class Meta:
        db_table = "locators"
        managed = False
        ordering = ["locator_rank", "id"]


class LocatorStat(models.Model):
    run_id = models.UUIDField(null=True, blank=True)
    record_id = models.UUIDField()
    step_no = models.IntegerField()
    strategy = models.TextField()
    locator = models.TextField()
    is_primary = models.BooleanField(default=False)
    locator_rank = models.IntegerField(null=True, blank=True)
    pos_x = models.FloatField(null=True, blank=True)
    pos_y = models.FloatField(null=True, blank=True)
    action = models.TextField(null=True, blank=True)
    page_url = models.TextField(null=True, blank=True)
    runner = models.TextField(null=True, blank=True)
    author = models.TextField(null=True, blank=True)
    folder_name = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField()
    tenant_id = models.UUIDField(null=True, blank=True, db_index=True)

    class Meta:
        db_table = "locators_stat"
        managed = False
        ordering = ["step_no"]


class Step(models.Model):
    record_id = models.UUIDField()
    step_no = models.IntegerField()
    action = models.TextField()
    page_url = models.TextField()
    element_tag = models.TextField(null=True, blank=True)
    locator_id = models.BigIntegerField(null=True, blank=True)
    data_id = models.BigIntegerField(null=True, blank=True)
    raw_event = models.JSONField()
    created_at = models.DateTimeField()
    recorder = models.TextField(null=True, blank=True)
    runner   = models.TextField(null=True, blank=True)
    author   = models.TextField(null=True, blank=True)
    folder_name = models.TextField(null=True, blank=True)
    headless_state   = models.BooleanField(default=False)
    file_order       = models.IntegerField(default=1)
    parent_folder_id = models.UUIDField(null=True, blank=True)
    sub_folder_id    = models.UUIDField(null=True, blank=True)
    end_folder_id    = models.UUIDField(null=True, blank=True)
    validation       = models.TextField(null=True, blank=True)
    steps_description = models.TextField(null=True, blank=True)
    page_title       = models.TextField(null=True, blank=True)
    playwright_code  = models.TextField(null=True, blank=True)
    raw_event_playwright = models.JSONField(null=True, blank=True)
    engine           = models.CharField(max_length=20, null=True, blank=True)
    tenant_id        = models.UUIDField(null=True, blank=True, db_index=True)

    class Meta:
        db_table = "steps"
        managed = False
        ordering = ["step_no"]

    def __str__(self):
        return f"[{self.step_no}] {self.action} — {self.page_url}"

    @property
    def primary_locator(self):
        if self.locator_id is None:
            return None
        try:
            return Locator.objects.get(id=self.locator_id)
        except Locator.DoesNotExist:
            return None

    @property
    def data_entry(self):
        if self.data_id is None:
            return None
        try:
            return DataEntry.objects.get(id=self.data_id)
        except DataEntry.DoesNotExist:
            return None


class Recording(models.Model):
    """Raw step data captured during a recording session (initial capture table)."""
    record_id  = models.UUIDField()
    step_no     = models.IntegerField()
    action      = models.TextField()
    page_url    = models.TextField()
    element_tag = models.TextField(null=True, blank=True)
    locator_id  = models.BigIntegerField(null=True, blank=True)
    data_id     = models.BigIntegerField(null=True, blank=True)
    raw_event   = models.JSONField()
    created_at  = models.DateTimeField()
    recorder    = models.TextField(null=True, blank=True)
    runner      = models.TextField(null=True, blank=True)
    folder_name = models.TextField(null=True, blank=True)
    headless_state = models.BooleanField(default=False)
    file_order  = models.IntegerField(default=1)
    playwright_code = models.TextField(null=True, blank=True)
    engine      = models.CharField(max_length=20, null=True, blank=True)
    tenant_id   = models.UUIDField(null=True, blank=True, db_index=True)

    class Meta:
        db_table = "recordings"
        managed  = False
        ordering = ["step_no"]

    def __str__(self):
        return f"[{self.step_no}] {self.action} — {self.page_url}"

    @property
    def primary_locator(self):
        if self.locator_id is None:
            return None
        try:
            return Locator.objects.get(id=self.locator_id)
        except Locator.DoesNotExist:
            return None

    @property
    def data_entry(self):
        if self.data_id is None:
            return None
        try:
            return DataEntry.objects.get(id=self.data_id)
        except DataEntry.DoesNotExist:
            return None


class RunResult(models.Model):
    STATUS_PASS         = "pass"
    STATUS_FAIL         = "fail"
    STATUS_NOT_EXECUTED = "not_executed"
    STATUS_CHOICES = [
        (STATUS_PASS,         "Pass"),
        (STATUS_FAIL,         "Fail"),
        (STATUS_NOT_EXECUTED, "Not Executed"),
    ]

    run_id      = models.UUIDField()
    record_id  = models.UUIDField()
    step_no     = models.IntegerField()
    action      = models.TextField()
    page_url    = models.TextField()
    element_tag = models.TextField(null=True, blank=True)
    locator_id  = models.BigIntegerField(null=True, blank=True)
    data_id     = models.BigIntegerField(null=True, blank=True)
    raw_event   = models.JSONField()
    status      = models.TextField(
        choices=STATUS_CHOICES,
        default=STATUS_NOT_EXECUTED,
    )
    message     = models.TextField(null=True, blank=True)
    runner      = models.TextField(null=True, blank=True)
    author      = models.TextField(null=True, blank=True)
    folder_name      = models.TextField(null=True, blank=True)
    parent_folder_id = models.UUIDField(null=True, blank=True)
    sub_folder_id    = models.UUIDField(null=True, blank=True)
    end_folder_id    = models.UUIDField(null=True, blank=True)
    run_date         = models.DateTimeField(null=True, blank=True)
    created_at       = models.DateTimeField(auto_now_add=True)
    validation       = models.TextField(null=True, blank=True)
    steps_description = models.TextField(null=True, blank=True)
    page_title       = models.TextField(null=True, blank=True)
    screenshot       = models.BinaryField(null=True, blank=True)
    engine           = models.CharField(max_length=20, null=True, blank=True)
    tenant_id        = models.UUIDField(null=True, blank=True, db_index=True)

    class Meta:
        db_table = "run_table"
        managed  = False
        ordering = ["run_id", "step_no"]

    def __str__(self):
        return f"[Run {self.run_id} | Step {self.step_no}] {self.action} — {self.status}"


class SessionMeta(models.Model):
    record_id        = models.UUIDField(primary_key=True)
    record_name      = models.TextField(default="")
    recorder         = models.TextField(null=True, blank=True)
    folder_name      = models.TextField(null=True, blank=True)
    parent_folder_id = models.UUIDField(null=True, blank=True)
    sub_folder_id    = models.UUIDField(null=True, blank=True)
    end_folder_id    = models.UUIDField(null=True, blank=True)
    engine           = models.CharField(max_length=20, null=True, blank=True)
    is_baseline      = models.BooleanField(default=False)
    created_at       = models.DateTimeField(auto_now_add=True)
    tenant_id        = models.UUIDField(null=True, blank=True, db_index=True)

    class Meta:
        db_table = "session_meta"
        managed  = False

    def __str__(self):
        return self.record_name or str(self.record_id)


class AppSetting(models.Model):
    key         = models.CharField(max_length=100, primary_key=True)
    value       = models.TextField(default="")
    label       = models.CharField(max_length=200, default="")
    description = models.TextField(default="")
    group_name  = models.CharField(max_length=100, default="General")
    input_type  = models.CharField(max_length=20, default="text")  # text|number|checkbox|select
    choices     = models.TextField(default="")  # JSON array for select type

    class Meta:
        db_table = "app_config"
        managed  = False

    def __str__(self):
        return f"{self.key} = {self.value}"


class RemoteExecution(models.Model):
    """Track remote execution history for IP/port caching."""
    user            = models.CharField(max_length=150, null=True, blank=True)
    remote_ip       = models.CharField(max_length=255)
    remote_port     = models.IntegerField(default=8888)
    record_id       = models.UUIDField(null=True, blank=True)
    headless        = models.BooleanField(default=False)
    created_at      = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "remote_executions"
        managed  = False
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.remote_ip}:{self.remote_port} @ {self.created_at}"


class RemoteTarget(models.Model):
    """Distinct remote targets (IP/hostname + port) saved for quick re-use."""
    remote_ip   = models.CharField(max_length=255)
    remote_port = models.IntegerField(default=8888)
    last_used   = models.DateTimeField(auto_now=True)

    class Meta:
        db_table       = "remote_targets"
        unique_together = [("remote_ip", "remote_port")]
        ordering       = ["-last_used"]

    def __str__(self):
        return f"{self.remote_ip}:{self.remote_port}"


class ChatMessage(models.Model):
    """Persisted chatbot conversation messages per user."""
    user = models.ForeignKey(
        "auth.User", on_delete=models.CASCADE, related_name="chat_messages"
    )
    role = models.CharField(max_length=10, choices=[("user", "User"), ("bot", "Bot")])
    content = models.TextField()
    download = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "chat_messages"
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.user.username} [{self.role}] {self.content[:50]}"
