import csv
import json
from functools import wraps
from datetime import timedelta
from html import escape as html_escape
from io import StringIO

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.db import models
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.text import slugify
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt, ensure_csrf_cookie
from django.views.decorators.http import require_POST
from openpyxl import Workbook

from .forms import AdminUserForm, DatabaseConnectionForm, ProjectFolderForm, TestCaseForm, ThemeForm
from .execution_manager import execution_manager
from .models import AuditLog, DatabaseConnection, ProjectFolder, TestCase, TestExecution, Theme, UserProfile, UserThemePreference
from .services import execute_test_case, test_database_connection


def get_client_ip(request):
    """Extract client IP address from request."""
    x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if x_forwarded_for:
        ip = x_forwarded_for.split(",")[0]
    else:
        ip = request.META.get("REMOTE_ADDR")
    return ip


def log_action(request, action, target_type, target_id=None, target_name="", details=""):
    """Log user actions for audit trail."""
    if not request.user.is_authenticated:
        return
    try:
        AuditLog.objects.create(
            user=request.user,
            action=action,
            target_type=target_type,
            target_id=target_id,
            target_name=target_name,
            details=details,
            ip_address=get_client_ip(request),
            user_agent=request.META.get("HTTP_USER_AGENT", "")[:500],
        )
    except Exception as e:
        pass  # Fail silently to not interrupt normal operations


def _execution_report_lines(execution):
    conn = execution.test_case.connection
    return [
        f"Execution ID: {execution.id}",
        f"Executed At: {execution.executed_at}",
        f"Status: {execution.status}",
        f"Test Case: {execution.test_case.name}",
        f"Test Type: {execution.test_case.get_test_type_display()}",
        f"Actual Value: {execution.actual_value or '-'}",
        f"Details: {execution.details or '-'}",
        "",
        "Connection Details",
        f"Connection Name: {conn.name}",
        f"Database Type: {conn.db_type}",
        f"Host: {conn.host}",
        f"Port: {conn.port}",
        f"Database Name: {conn.database_name or '-'}",
        f"Service Name: {conn.service_name or '-'}",
        f"Username: {conn.username}",
    ]


def _status_theme(status):
    status = (status or "").upper()
    if status == "PASS":
        return {
            "fg": "#166534",
            "bg": "#dcfce7",
            "border": "#86efac",
        }
    if status == "FAIL":
        return {
            "fg": "#b91c1c",
            "bg": "#fee2e2",
            "border": "#fca5a5",
        }
    return {
        "fg": "#1d4ed8",
        "bg": "#dbeafe",
        "border": "#93c5fd",
    }


def _execution_report_payload(execution):
    conn = execution.test_case.connection
    actual_text = execution.actual_value or "-"
    return {
        "execution_id": str(execution.id),
        "executed_at": execution.executed_at.isoformat(),
        "status": execution.status,
        "test_case": execution.test_case.name,
        "test_type": execution.test_case.get_test_type_display(),
        "actual_value": actual_text,
        "query_return_results": _actual_value_to_csv_text(actual_text),
        "details": execution.details or "-",
        "query": execution.test_case.query or "-",
        "connection": {
            "name": conn.name,
            "db_type": conn.db_type,
            "host": conn.host,
            "port": str(conn.port),
            "database_name": conn.database_name or "-",
            "service_name": conn.service_name or "-",
            "username": conn.username,
        },
    }


def _actual_value_to_csv_text(actual_value):
    # Keep field content intact; only normalize outer line breaks.
    value = (actual_value or "").rstrip("\r\n")
    if not value:
        return ""

    # Legacy QUERY_VALUE payloads were pipe-delimited; convert these first.
    if " | " in value:
        values = value.split(" | ")
        out = StringIO()
        writer = csv.writer(out)
        writer.writerow([f"col_{i}" for i in range(1, len(values) + 1)])
        writer.writerow(values)
        return out.getvalue().strip("\r\n")

    # If content already looks like CSV, keep it as-is.
    if "," in value and "\n" in value:
        return value

    parsed = None
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        parsed = None

    if parsed is not None:
        out = StringIO()
        writer = csv.writer(out)

        if isinstance(parsed, dict):
            headers = list(parsed.keys())
            writer.writerow(headers)
            writer.writerow([parsed.get(h, "") for h in headers])
            return out.getvalue().strip("\r\n")

        if isinstance(parsed, list):
            if parsed and all(isinstance(item, dict) for item in parsed):
                headers = list(parsed[0].keys())
                writer.writerow(headers)
                for row in parsed:
                    writer.writerow([row.get(h, "") for h in headers])
                return out.getvalue().strip("\r\n")

            writer.writerow(["value"])
            for item in parsed:
                writer.writerow([item])
            return out.getvalue().strip("\r\n")

    out = StringIO()
    writer = csv.writer(out)
    writer.writerow(["actual_value"])
    writer.writerow([value])
    return out.getvalue().strip("\r\n")


def _download_base_name(test_case_name):
    base = slugify(test_case_name or "")
    return base or "test-case"


def _pdf_escape(value):
    return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _simple_pdf_from_lines(lines):
    max_lines = 44
    prepared = list(lines[:max_lines])
    if len(lines) > max_lines:
        prepared.append("...")

    stream_lines = ["BT", "/F1 10 Tf", "40 790 Td", "12 TL"]
    for line in prepared:
        stream_lines.append(f"({_pdf_escape(line)}) Tj")
        stream_lines.append("T*")
    stream_lines.append("ET")
    content_stream = "\n".join(stream_lines).encode("latin-1", "replace")

    objs = []
    objs.append(b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n")
    objs.append(b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n")
    objs.append(
        b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >> endobj\n"
    )
    objs.append(b"4 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj\n")
    objs.append(
        b"5 0 obj << /Length "
        + str(len(content_stream)).encode("ascii")
        + b" >> stream\n"
        + content_stream
        + b"\nendstream endobj\n"
    )

    header = b"%PDF-1.4\n"
    pdf = bytearray(header)
    offsets = [0]
    for obj in objs:
        offsets.append(len(pdf))
        pdf.extend(obj)

    xref_start = len(pdf)
    pdf.extend(b"xref\n0 6\n")
    pdf.extend(b"0000000000 65535 f \n")
    for i in range(1, 6):
        pdf.extend(f"{offsets[i]:010d} 00000 n \n".encode("ascii"))
    pdf.extend(b"trailer << /Size 6 /Root 1 0 R >>\n")
    pdf.extend(f"startxref\n{xref_start}\n%%EOF".encode("ascii"))
    return bytes(pdf)


def _wrap_text(value, width=86):
    text = (value or "").strip()
    if not text:
        return ["-"]

    words = text.split()
    lines = []
    current = ""

    def push_chunks(token):
        if len(token) <= width:
            return [token]
        return [token[i : i + width] for i in range(0, len(token), width)]

    for word in words:
        for chunk in push_chunks(word):
            if not current:
                current = chunk
                continue
            if len(current) + 1 + len(chunk) <= width:
                current += " " + chunk
            else:
                lines.append(current)
                current = chunk

    if current:
        lines.append(current)
    return lines


def _preview_lines(value, width=84, max_lines=10):
    text = (value or "").strip()
    if not text:
        return ["-"]

    lines = []
    for raw_line in text.splitlines() or [text]:
        lines.extend(_wrap_text(raw_line, width))
        if len(lines) >= max_lines:
            break

    if len(lines) > max_lines:
        lines = lines[:max_lines]

    remaining = "\n".join(text.splitlines()[max_lines:]).strip()
    if len(lines) == max_lines and (remaining or len(text) > 4000):
        lines[-1] = (lines[-1][: max(0, width - 3)] + "...") if len(lines[-1]) >= width else lines[-1] + " ..."

    return lines


def _modern_pdf_from_payload(payload):
    actual_preview = payload["actual_value"]
    if len(actual_preview) > 140:
        actual_preview = actual_preview[:137] + "..."

    sections = [
        (
            "Execution Snapshot",
            [
                f"Execution ID: {payload['execution_id']}",
                f"Executed At: {payload['executed_at']}",
                f"Status: {payload['status']}",
                f"Test Case: {payload['test_case']}",
                f"Test Type: {payload['test_type']}",
                f"Actual Value: {actual_preview}",
            ],
        ),
        (
            "Connection",
            [
                f"Name: {payload['connection']['name']}",
                f"DB Type: {payload['connection']['db_type']}",
                f"Host: {payload['connection']['host']}",
                f"Port: {payload['connection']['port']}",
                f"Database: {payload['connection']['database_name']}",
                f"Service: {payload['connection']['service_name']}",
                f"Username: {payload['connection']['username']}",
            ],
        ),
        (
            "Result",
            [
                "Details:",
                *_preview_lines(payload["details"], 84, 5),
                "",
                "Query:",
                *_preview_lines(payload["query"], 84, 4),
                "",
                "Query Return/Results:",
                *_preview_lines(payload.get("query_return_results") or payload.get("actual_value"), 84, 6),
            ],
        ),
    ]

    ops = []

    def add_rect(x, y, w, h, fill_rgb, stroke_rgb=None):
        ops.append(f"{fill_rgb[0]} {fill_rgb[1]} {fill_rgb[2]} rg")
        ops.append(f"{x} {y} {w} {h} re f")
        if stroke_rgb is not None:
            ops.append(f"{stroke_rgb[0]} {stroke_rgb[1]} {stroke_rgb[2]} RG")
            ops.append(f"{x} {y} {w} {h} re S")

    def add_text(x, y, text, size=10, bold=False, color=(0.14, 0.17, 0.21)):
        font = "F2" if bold else "F1"
        safe = _pdf_escape(text)
        ops.append("BT")
        ops.append(f"/{font} {size} Tf")
        ops.append(f"{color[0]} {color[1]} {color[2]} rg")
        ops.append(f"1 0 0 1 {x} {y} Tm")
        ops.append(f"({safe}) Tj")
        ops.append("ET")

    # Page and header band.
    add_rect(0, 0, 612, 792, (0.98, 0.99, 0.99))
    # Subtle title watermark behind content.
    add_text(120, 420, "DB TestLab", 54, bold=True, color=(0.90, 0.94, 0.93))
    add_text(172, 390, "Execution Report", 22, bold=True, color=(0.91, 0.95, 0.94))
    add_rect(28, 738, 556, 38, (0.07, 0.46, 0.43))
    add_text(44, 752, "DB TestLab - Execution Report", 14, bold=True, color=(1, 1, 1))
    add_text(392, 752, f"#{payload['execution_id']}", 11, bold=True, color=(0.9, 0.97, 0.95))

    y_cursor = 710
    section_colors = [
        ((0.93, 0.97, 0.96), (0.72, 0.86, 0.82)),
        ((0.94, 0.96, 0.99), (0.74, 0.82, 0.94)),
        ((0.99, 0.97, 0.93), (0.95, 0.84, 0.67)),
    ]

    for idx, (title, lines) in enumerate(sections):
        visible_lines = []
        for line in lines:
            visible_lines.extend(_wrap_text(line, 82))
            if len(visible_lines) >= 14:
                visible_lines = visible_lines[:14]
                break
        card_h = 28 + (len(visible_lines) * 13)
        y0 = y_cursor - card_h
        fill, border = section_colors[idx % len(section_colors)]
        add_rect(36, y0, 540, card_h, fill, border)
        add_text(50, y_cursor - 18, title, 11, bold=True, color=(0.07, 0.33, 0.31))

        line_y = y_cursor - 34
        for line in visible_lines:
            add_text(50, line_y, line if line else " ", 9, color=(0.17, 0.21, 0.27))
            line_y -= 13

        y_cursor = y0 - 14

    content_stream = "\n".join(ops).encode("latin-1", "replace")

    objs = []
    objs.append(b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n")
    objs.append(b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n")
    objs.append(
        b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 4 0 R /F2 6 0 R >> >> /Contents 5 0 R >> endobj\n"
    )
    objs.append(b"4 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj\n")
    objs.append(
        b"5 0 obj << /Length "
        + str(len(content_stream)).encode("ascii")
        + b" >> stream\n"
        + content_stream
        + b"\nendstream endobj\n"
    )
    objs.append(b"6 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >> endobj\n")

    header = b"%PDF-1.4\n"
    pdf = bytearray(header)
    offsets = [0]
    for obj in objs:
        offsets.append(len(pdf))
        pdf.extend(obj)

    xref_start = len(pdf)
    pdf.extend(b"xref\n0 7\n")
    pdf.extend(b"0000000000 65535 f \n")
    for i in range(1, 7):
        pdf.extend(f"{offsets[i]:010d} 00000 n \n".encode("ascii"))
    pdf.extend(b"trailer << /Size 7 /Root 1 0 R >>\n")
    pdf.extend(f"startxref\n{xref_start}\n%%EOF".encode("ascii"))
    return bytes(pdf)


def role_required(*allowed_roles):
    def decorator(view_func):
        @wraps(view_func)
        @login_required
        def _wrapped(request, *args, **kwargs):
            if request.user.is_superuser:
                return view_func(request, *args, **kwargs)
            
            # Check UserProfile role first
            try:
                user_profile = request.user.db_profile
                if user_profile.role in allowed_roles:
                    return view_func(request, *args, **kwargs)
            except UserProfile.DoesNotExist:
                pass
            
            # Fall back to Django groups
            if request.user.groups.filter(name__in=allowed_roles).exists():
                return view_func(request, *args, **kwargs)
            
            messages.error(request, "You do not have permission to perform this action.")
            return redirect("db:dashboard")

        return _wrapped

    return decorator


def can_manage_tests(user):
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    try:
        return user.db_profile.role in [UserProfile.Role.ADMIN, UserProfile.Role.TESTER]
    except UserProfile.DoesNotExist:
        return user.groups.filter(name__in=["Admin", "Tester"]).exists()


def is_admin_user(user):
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    try:
        return user.db_profile.role == UserProfile.Role.ADMIN
    except UserProfile.DoesNotExist:
        return user.groups.filter(name="Admin").exists()


def _build_testcase_tree(active_only=False, testcase_qs=None):
    folder_qs = ProjectFolder.objects.select_related("parent").all().order_by("sort_order", "name")
    if testcase_qs is None:
        testcase_qs = TestCase.objects.select_related("connection", "project_folder").all().order_by("sort_order", "name")

    if active_only:
        testcase_qs = testcase_qs.filter(is_active=True)

    folders = list(folder_qs)
    testcases = list(testcase_qs)

    folders_by_parent = {}
    for folder in folders:
        folders_by_parent.setdefault(folder.parent_id, []).append(folder)

    tests_by_folder = {}
    root_tests = []
    for test in testcases:
        if test.project_folder_id is None:
            root_tests.append(test)
        else:
            tests_by_folder.setdefault(test.project_folder_id, []).append(test)

    def build_nodes(parent_id):
        nodes = []
        for folder in folders_by_parent.get(parent_id, []):
            nodes.append(
                {
                    "folder": folder,
                    "children": build_nodes(folder.id),
                    "tests": tests_by_folder.get(folder.id, []),
                }
            )
        return nodes

    return {
        "folders": build_nodes(None),
        "root_tests": root_tests,
    }


def _serial_active_testcase_ids_from_hierarchy():
    tree = _build_testcase_tree(active_only=True)
    ordered_ids = []

    for test in tree["root_tests"]:
        ordered_ids.append(test.id)

    def visit(nodes):
        for node in nodes:
            for test in node["tests"]:
                ordered_ids.append(test.id)
            visit(node["children"])

    visit(tree["folders"])
    return ordered_ids


def _is_descendant(parent_candidate, folder):
    current = parent_candidate
    while current is not None:
        if current.id == folder.id:
            return True
        current = current.parent
    return False


def _next_folder_order(parent_id):
    qs = ProjectFolder.objects.filter(parent_id=parent_id).order_by("-sort_order")
    first = qs.first()
    return (first.sort_order + 1) if first else 1


def _next_testcase_order(folder_id):
    qs = TestCase.objects.filter(project_folder_id=folder_id).order_by("-sort_order")
    first = qs.first()
    return (first.sort_order + 1) if first else 1


def _unique_folder_name(parent_id, base_name):
    candidate = base_name
    i = 2
    while ProjectFolder.objects.filter(parent_id=parent_id, name=candidate).exists():
        candidate = f"{base_name} {i}"
        i += 1
    return candidate


def _unique_testcase_name(base_name):
    candidate = base_name
    i = 2
    while TestCase.objects.filter(name=candidate).exists():
        candidate = f"{base_name} {i}"
        i += 1
    return candidate


def _clone_folder_subtree(source_folder, target_parent):
    new_folder = ProjectFolder.objects.create(
        name=_unique_folder_name(target_parent.id if target_parent else None, f"{source_folder.name} Copy"),
        parent=target_parent,
        sort_order=_next_folder_order(target_parent.id if target_parent else None),
    )

    testcases = TestCase.objects.filter(project_folder=source_folder).order_by("sort_order", "name")
    for test in testcases:
        TestCase.objects.create(
            name=_unique_testcase_name(f"{test.name} Copy"),
            project_folder=new_folder,
            sort_order=_next_testcase_order(new_folder.id),
            connection=test.connection,
            test_type=test.test_type,
            table_name=test.table_name,
            query=test.query,
            expected_value=test.expected_value,
            comparison_operator=test.comparison_operator,
            is_active=test.is_active,
            notes=test.notes,
        )

    children = ProjectFolder.objects.filter(parent=source_folder).order_by("sort_order", "name")
    for child in children:
        _clone_folder_subtree(child, new_folder)

    return new_folder


def _resequence_folders(parent_id, ordered_ids=None):
    qs = ProjectFolder.objects.filter(parent_id=parent_id).order_by("sort_order", "id")
    if ordered_ids:
        folder_map = {f.id: f for f in qs}
        sequence = [folder_map[i] for i in ordered_ids if i in folder_map]
        sequence.extend([f for f in qs if f.id not in ordered_ids])
    else:
        sequence = list(qs)

    for idx, folder in enumerate(sequence, start=1):
        expected = idx * 10
        if folder.sort_order != expected:
            folder.sort_order = expected
            folder.save(update_fields=["sort_order", "updated_at"])


def _resequence_testcases(folder_id, ordered_ids=None):
    qs = TestCase.objects.filter(project_folder_id=folder_id).order_by("sort_order", "id")
    if ordered_ids:
        tc_map = {t.id: t for t in qs}
        sequence = [tc_map[i] for i in ordered_ids if i in tc_map]
        sequence.extend([t for t in qs if t.id not in ordered_ids])
    else:
        sequence = list(qs)

    for idx, testcase in enumerate(sequence, start=1):
        expected = idx * 10
        if testcase.sort_order != expected:
            testcase.sort_order = expected
            testcase.save(update_fields=["sort_order", "updated_at"])


def admin_required(view_func):
    @wraps(view_func)
    @login_required
    def _wrapped(request, *args, **kwargs):
        if not is_admin_user(request.user):
            messages.error(request, "Administrator access required.")
            return redirect("db:dashboard")
        return view_func(request, *args, **kwargs)

    return _wrapped


@login_required
def dashboard(request):
    total_test_cases = TestCase.objects.count()
    total_executions = TestExecution.objects.count()
    passed_tests = TestExecution.objects.filter(status=TestExecution.Status.PASS).count()
    failed_tests = TestExecution.objects.filter(status=TestExecution.Status.FAIL).count()
    error_tests = TestExecution.objects.filter(status=TestExecution.Status.ERROR).count()

    today = timezone.now().date()
    start_date = today - timedelta(days=6)
    recent_window = TestExecution.objects.filter(executed_at__date__gte=start_date)

    daily_stats = []
    for i in range(7):
        day = start_date + timedelta(days=i)
        day_qs = recent_window.filter(executed_at__date=day)
        daily_stats.append(
            {
                "date": day.strftime("%b %d"),
                "passed": day_qs.filter(status=TestExecution.Status.PASS).count(),
                "failed": day_qs.filter(status=TestExecution.Status.FAIL).count(),
                "error": day_qs.filter(status=TestExecution.Status.ERROR).count(),
            }
        )

    context = {
        "connection_count": DatabaseConnection.objects.count(),
        "total_test_cases": total_test_cases,
        "total_executions": total_executions,
        "passed_tests": passed_tests,
        "failed_tests": failed_tests,
        "error_tests": error_tests,
        "daily_stats": json.dumps(daily_stats),
        "recent_executions": TestExecution.objects.select_related("test_case")[:10],
    }
    return render(request, "db/dashboard.html", context)


@login_required
def connection_list(request):
    connections = DatabaseConnection.objects.all().order_by("name")
    return render(
        request,
        "db/connections/list.html",
        {
            "connections": connections,
            "can_manage": can_manage_tests(request.user),
        },
    )


@login_required
def connection_detail(request, pk):
    instance = get_object_or_404(DatabaseConnection, pk=pk)
    return render(
        request,
        "db/connections/detail.html",
        {
            "connection": instance,
            "can_manage": can_manage_tests(request.user),
        },
    )


@role_required("Admin", "Tester")
def connection_create(request):
    if request.method == "POST":
        form = DatabaseConnectionForm(request.POST)
        if form.is_valid():
            instance = form.save()
            log_action(request, AuditLog.Action.CREATE, "DatabaseConnection", target_id=instance.id, target_name=instance.name)
            messages.success(request, "Database connection saved.")
            return redirect("db:connection_list")
    else:
        form = DatabaseConnectionForm()
    return render(request, "db/connections/form.html", {"form": form, "title": "New Connection"})


@role_required("Admin", "Tester")
def connection_edit(request, pk):
    instance = get_object_or_404(DatabaseConnection, pk=pk)
    if request.method == "POST":
        form = DatabaseConnectionForm(request.POST, instance=instance)
        if form.is_valid():
            form.save()
            log_action(request, AuditLog.Action.UPDATE, "DatabaseConnection", target_id=instance.id, target_name=instance.name)
            messages.success(request, "Database connection updated.")
            return redirect("db:connection_list")
    else:
        form = DatabaseConnectionForm(instance=instance)
    return render(
        request,
        "db/connections/form.html",
        {
            "form": form,
            "title": "Edit Connection",
            "connection": instance,
            "is_edit": True,
        },
    )


@csrf_exempt
@role_required("Admin", "Tester")
def connection_test(request, pk):
    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"
    if request.method != "POST":
        if is_ajax:
            return JsonResponse({"ok": False, "detail": "Invalid request method."}, status=405)
        messages.error(request, "Invalid request method for connection test.")
        return redirect("db:connection_edit", pk=pk)

    instance = get_object_or_404(DatabaseConnection, pk=pk)
    ok, detail = test_database_connection(instance)

    log_action(
        request,
        AuditLog.Action.TEST_CONNECTION,
        "DatabaseConnection",
        target_id=instance.id,
        target_name=instance.name,
        details=f"Success: {ok}",
    )

    if is_ajax:
        return JsonResponse({"ok": ok, "detail": detail})

    if ok:
        messages.success(request, f"{instance.name}: {detail}")
    else:
        messages.error(request, f"{instance.name}: Connection failed - {detail}")
    return redirect("db:connection_edit", pk=pk)


@csrf_exempt
@role_required("Admin", "Tester")
def connection_test_unsaved(request):
    if request.method != "POST":
        return JsonResponse({"ok": False, "detail": "Invalid request method."}, status=405)

    try:
        port = int(request.POST.get("port") or 0)
    except ValueError:
        port = 0

    conn = DatabaseConnection(
        name=request.POST.get("name") or "Untested",
        db_type=request.POST.get("db_type", ""),
        host=request.POST.get("host", ""),
        port=port,
        database_name=request.POST.get("database_name", ""),
        service_name=request.POST.get("service_name", ""),
        username=request.POST.get("username", ""),
        password=request.POST.get("password", ""),
    )
    ok, detail = test_database_connection(conn)
    return JsonResponse({"ok": ok, "detail": detail})


@login_required
@ensure_csrf_cookie
def testcase_list(request):
    search = (request.GET.get("search") or "").strip()
    test_type = (request.GET.get("type") or "").strip()
    connection_id = (request.GET.get("connection") or "").strip()

    testcase_qs = TestCase.objects.select_related("connection", "project_folder").all().order_by("sort_order", "name")

    if search:
        testcase_qs = testcase_qs.filter(name__icontains=search)

    valid_types = {choice[0] for choice in TestCase.TestType.choices}
    if test_type in valid_types:
        testcase_qs = testcase_qs.filter(test_type=test_type)

    if connection_id.isdigit():
        testcase_qs = testcase_qs.filter(connection_id=int(connection_id))

    tree = _build_testcase_tree(active_only=False, testcase_qs=testcase_qs)
    return render(
        request,
        "db/testcases/list.html",
        {
            "folder_tree": tree["folders"],
            "root_tests": tree["root_tests"],
            "folders": ProjectFolder.objects.all().order_by("name"),
            "folder_form": ProjectFolderForm(),
            "connections": DatabaseConnection.objects.all().order_by("name"),
            "test_types": TestCase.TestType.choices,
            "filters": {
                "search": search,
                "type": test_type,
                "connection": connection_id,
            },
            "can_manage": can_manage_tests(request.user),
        },
    )


@role_required("Admin", "Tester")
def testcase_create(request):
    if request.method == "POST":
        form = TestCaseForm(request.POST)
        if form.is_valid():
            instance = form.save()
            log_action(request, AuditLog.Action.CREATE, "TestCase", target_id=instance.id, target_name=instance.name)
            messages.success(request, "Test case saved.")
            return redirect("db:testcase_list")
    else:
        form = TestCaseForm()
    return render(request, "db/testcases/form.html", {"form": form, "title": "New Test Case"})


@login_required
def testcase_detail(request, pk):
    testcase = get_object_or_404(TestCase.objects.select_related("connection", "project_folder"), pk=pk)
    recent_transactions = list(
        TestExecution.objects.filter(test_case=testcase)
        .order_by("-executed_at")[:10]
    )
    return render(
        request,
        "db/testcases/detail.html",
        {
            "testcase": testcase,
            "recent_transactions": recent_transactions,
            "can_manage": can_manage_tests(request.user),
        },
    )


@role_required("Admin", "Tester")
def testcase_edit(request, pk):
    instance = get_object_or_404(TestCase, pk=pk)
    if request.method == "POST":
        form = TestCaseForm(request.POST, instance=instance)
        if form.is_valid():
            form.save()
            log_action(request, AuditLog.Action.UPDATE, "TestCase", target_id=instance.id, target_name=instance.name)
            messages.success(request, "Test case updated.")
            return redirect("db:testcase_list")
    else:
        form = TestCaseForm(instance=instance)
    return render(request, "db/testcases/form.html", {"form": form, "title": "Edit Test Case"})


@role_required("Admin", "Tester")
def testcase_run(request, pk):
    test_case = get_object_or_404(TestCase, pk=pk)
    status, details, actual = execute_test_case(test_case)
    execution = TestExecution.objects.create(
        test_case=test_case,
        status=status,
        details=details,
        actual_value=actual,
    )
    log_action(request, AuditLog.Action.EXECUTE, "TestCase", target_id=test_case.id, target_name=test_case.name, details=f"Status: {status}")
    if status == TestExecution.Status.PASS:
        messages.success(request, f"{test_case.name}: PASS")
    elif status == TestExecution.Status.FAIL:
        messages.warning(request, f"{test_case.name}: FAIL - {details}")
    else:
        messages.error(request, f"{test_case.name}: ERROR - {details}")
    return redirect("db:execution_list")


@role_required("Admin", "Tester")
def testcase_run_all(request):
    active_cases = TestCase.objects.select_related("connection").filter(is_active=True).order_by("name")

    results = []
    pass_count = 0
    fail_count = 0
    error_count = 0

    for test_case in active_cases:
        status, details, actual = execute_test_case(test_case)
        TestExecution.objects.create(
            test_case=test_case,
            status=status,
            details=details,
            actual_value=actual,
        )
        if status == TestExecution.Status.PASS:
            pass_count += 1
        elif status == TestExecution.Status.FAIL:
            fail_count += 1
        else:
            error_count += 1
        results.append(
            {
                "name": test_case.name,
                "status": status,
                "details": details,
                "actual": actual,
                "connection": test_case.connection.name,
            }
        )

    summary = {
        "total": len(results),
        "pass": pass_count,
        "fail": fail_count,
        "error": error_count,
    }

    if summary["total"] == 0:
        messages.warning(request, "No active test cases found.")

    return render(
        request,
        "db/testcases/run_all_summary.html",
        {
            "results": results,
            "summary": summary,
        },
    )


@role_required("Admin", "Tester")
def testcase_run_all_start(request):
    if request.method != "POST":
        return redirect("db:testcase_list")

    mode = (request.POST.get("mode") or "serial").strip().lower()
    if mode not in {"serial", "parallel"}:
        mode = "serial"

    if mode == "serial":
        active_ids = _serial_active_testcase_ids_from_hierarchy()
    else:
        active_ids = list(TestCase.objects.filter(is_active=True).order_by("sort_order", "name").values_list("id", flat=True))

    if not active_ids:
        messages.warning(request, "No active test cases found.")
        return redirect("db:testcase_list")

    run_id = execution_manager.start_run(active_ids, mode=mode, requested_by=request.user.username)
    messages.success(request, f"Started {mode} execution run ({run_id[:8]}).")
    return redirect("db:testcase_list")


@role_required("Admin", "Tester")
@require_POST
def testcase_run_selected_start(request):
    mode = (request.POST.get("mode") or "serial").strip().lower()
    if mode not in {"serial", "parallel"}:
        mode = "serial"

    ids = [i for i in request.POST.getlist("test_case_ids") if i]
    if not ids:
        return JsonResponse({"ok": False, "message": "No test cases selected."}, status=400)

    selected_ids = list(
        TestCase.objects.filter(pk__in=ids).order_by("sort_order", "name").values_list("id", flat=True)
    )
    if not selected_ids:
        return JsonResponse({"ok": False, "message": "Selected test cases were not found."}, status=404)

    run_id = execution_manager.start_run(selected_ids, mode=mode, requested_by=request.user.username)
    return JsonResponse({"ok": True, "run_id": run_id, "message": f"Started {mode} run."})


@login_required
@require_POST
def testcase_execute_ajax(request):
    """Execute a single test case via AJAX and return JSON result."""
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, TypeError):
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    test_case_id = data.get("test_case_id")
    if not test_case_id:
        return JsonResponse({"error": "test_case_id is required"}, status=400)

    tc = get_object_or_404(TestCase, pk=test_case_id)
    status, details, actual = execute_test_case(tc)
    execution = TestExecution.objects.create(
        test_case=tc,
        status=status,
        details=details,
        actual_value=actual,
    )
    log_action(request, AuditLog.Action.EXECUTE, "TestCase", target_id=tc.id, target_name=tc.name, details=f"Status: {status}")

    return JsonResponse({
        "ok": True,
        "execution_id": execution.id,
        "result_status": status,
        "details": details,
        "actual_value": actual,
    })


def testcase_latest_results(request):
    """Get latest execution result for each requested test case (JSON API)."""
    ids_param = request.GET.get("ids", "").strip()
    if not ids_param:
        return JsonResponse({"results": {}})

    raw_ids = [s.strip() for s in ids_param.split(",") if s.strip()]
    valid_ids = []
    for raw_id in raw_ids:
        try:
            valid_ids.append(int(raw_id))
        except (ValueError, TypeError):
            pass

    if not valid_ids:
        return JsonResponse({"results": {}})

    # Get latest execution for each test case
    results = {}
    for tc_id in valid_ids:
        try:
            latest_exec = TestExecution.objects.filter(test_case_id=tc_id).order_by("-executed_at").first()
            if latest_exec:
                results[str(tc_id)] = latest_exec.status or "unknown"
            else:
                results[str(tc_id)] = "no_run"
        except Exception:
            results[str(tc_id)] = "unknown"

    return JsonResponse({"results": results})


def testcase_recent_executions(request, pk):
    """Return the most recent execution details for a DB test case (JSON API for Projects dropdown)."""
    tc = get_object_or_404(TestCase, pk=pk)
    latest_exec = TestExecution.objects.filter(test_case=tc).order_by("-executed_at").first()

    rows = []
    if latest_exec:
        executed_by = ""
        try:
            # AuditLog may record the user who triggered this execution
            log = AuditLog.objects.filter(
                target_type="TestCase", target_id=tc.id, action=AuditLog.Action.EXECUTE
            ).order_by("-timestamp").first()
            if log:
                executed_by = log.user.username if log.user else ""
        except Exception:
            pass

        rows.append({
            "id": latest_exec.id,
            "status": latest_exec.status,
            "connection": str(tc.connection) if tc.connection else "-",
            "executed_at": latest_exec.executed_at.strftime("%Y-%m-%d %H:%M:%S") if latest_exec.executed_at else "-",
            "executed_by": executed_by,
        })

    return JsonResponse({"rows": rows})


@role_required("Admin", "Tester")
@require_POST
def project_folder_create(request):
    form = ProjectFolderForm(request.POST)
    if form.is_valid():
        folder = form.save(commit=False)
        if folder.sort_order == 0:
            folder.sort_order = _next_folder_order(folder.parent_id)
        folder.save()
        messages.success(request, "Project folder created.")
    else:
        details = []
        for field, errors in form.errors.items():
            label = "Project name" if field == "name" else field.replace("_", " ").title()
            for error in errors:
                details.append(f"{label}: {error}")
        detail_text = " ".join(details) if details else "Please check the values."
        messages.error(request, f"Could not create folder. {detail_text}")
    return redirect("db:testcase_list")


@role_required("Admin", "Tester")
@require_POST
def project_folder_edit(request, pk):
    folder = get_object_or_404(ProjectFolder, pk=pk)
    new_name = (request.POST.get("name") or "").strip()
    if not new_name:
        messages.error(request, "Project name is required.")
        return redirect("db:testcase_list")

    if ProjectFolder.objects.filter(parent_id=folder.parent_id, name=new_name).exclude(pk=folder.pk).exists():
        messages.error(request, "A project with this name already exists at the same level.")
        return redirect("db:testcase_list")

    folder.name = new_name
    folder.save(update_fields=["name", "updated_at"])
    messages.success(request, "Project updated.")
    return redirect("db:testcase_list")


@role_required("Admin", "Tester")
@require_POST
def project_folder_duplicate(request, pk):
    folder = get_object_or_404(ProjectFolder, pk=pk)
    _clone_folder_subtree(folder, folder.parent)
    messages.success(request, "Project duplicated.")
    return redirect("db:testcase_list")


@role_required("Admin", "Tester")
@require_POST
def project_folder_delete(request, pk):
    folder = get_object_or_404(ProjectFolder, pk=pk)

    descendant_ids = []
    stack = [folder]
    while stack:
        current = stack.pop()
        descendant_ids.append(current.id)
        stack.extend(ProjectFolder.objects.filter(parent=current))

    TestCase.objects.filter(project_folder_id__in=descendant_ids).update(project_folder=None)
    folder.delete()
    messages.success(request, "Project deleted. Related test cases were moved to Ungrouped.")
    return redirect("db:testcase_list")


@role_required("Admin", "Tester")
@require_POST
def ungrouped_delete(request):
    count, _ = TestCase.objects.filter(project_folder__isnull=True).delete()
    messages.success(request, f"Deleted {count} ungrouped test case(s).")
    return redirect("db:testcase_list")


@role_required("Admin", "Tester")
@require_POST
def testcase_duplicate(request, pk):
    source = get_object_or_404(TestCase, pk=pk)
    copy = TestCase.objects.create(
        name=_unique_testcase_name(f"{source.name} Copy"),
        project_folder=source.project_folder,
        sort_order=_next_testcase_order(source.project_folder_id),
        connection=source.connection,
        test_type=source.test_type,
        table_name=source.table_name,
        query=source.query,
        expected_value=source.expected_value,
        comparison_operator=source.comparison_operator,
        is_active=source.is_active,
        notes=source.notes,
    )
    messages.success(request, f"Test case duplicated: {copy.name}")
    return redirect("db:testcase_list")


@role_required("Admin", "Tester")
@require_POST
def testcase_delete(request, pk):
    testcase = get_object_or_404(TestCase, pk=pk)
    testcase_name = testcase.name
    testcase.delete()
    log_action(request, AuditLog.Action.DELETE, "TestCase", target_id=pk, target_name=testcase_name)
    messages.success(request, "Test case deleted.")
    return redirect("db:testcase_list")


@role_required("Admin", "Tester")
@require_POST
def testcase_tree_move(request):
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "message": "Invalid JSON payload."}, status=400)
    item_type = (payload.get("item_type") or "").strip().lower()
    item_id = payload.get("item_id")
    target_folder_id = payload.get("target_folder_id")
    before_item_id = payload.get("before_item_id")

    if target_folder_id in {"", None}:
        target_folder = None
    else:
        target_folder = ProjectFolder.objects.select_related("parent").filter(pk=target_folder_id).first()
        if not target_folder:
            return JsonResponse({"ok": False, "message": "Target folder not found."}, status=404)

    if item_type == "folder":
        folder = ProjectFolder.objects.select_related("parent").filter(pk=item_id).first()
        if not folder:
            return JsonResponse({"ok": False, "message": "Folder not found."}, status=404)
        if target_folder and _is_descendant(target_folder, folder):
            return JsonResponse({"ok": False, "message": "Cannot move folder into itself or its child."}, status=400)
        source_parent_id = folder.parent_id
        folder.parent = target_folder
        folder.sort_order = _next_folder_order(target_folder.id if target_folder else None)
        folder.save(update_fields=["parent", "sort_order", "updated_at"])

        target_parent_id = target_folder.id if target_folder else None
        if before_item_id:
            siblings = list(ProjectFolder.objects.filter(parent_id=target_parent_id).order_by("sort_order", "id"))
            before_index = next((idx for idx, s in enumerate(siblings) if str(s.id) == str(before_item_id)), None)
            moving_index = next((idx for idx, s in enumerate(siblings) if s.id == folder.id), None)
            if before_index is not None and moving_index is not None:
                moving = siblings.pop(moving_index)
                if moving_index < before_index:
                    before_index -= 1
                siblings.insert(before_index, moving)
                _resequence_folders(target_parent_id, ordered_ids=[s.id for s in siblings])
            else:
                _resequence_folders(target_parent_id)
        else:
            _resequence_folders(target_parent_id)

        if source_parent_id != target_parent_id:
            _resequence_folders(source_parent_id)

        return JsonResponse({"ok": True, "message": "Folder moved."})

    if item_type == "testcase":
        testcase = TestCase.objects.filter(pk=item_id).first()
        if not testcase:
            return JsonResponse({"ok": False, "message": "Test case not found."}, status=404)
        source_folder_id = testcase.project_folder_id
        testcase.project_folder = target_folder
        testcase.sort_order = _next_testcase_order(target_folder.id if target_folder else None)
        testcase.save(update_fields=["project_folder", "sort_order", "updated_at"])

        target_id = target_folder.id if target_folder else None
        if before_item_id:
            siblings = list(TestCase.objects.filter(project_folder_id=target_id).order_by("sort_order", "id"))
            before_index = next((idx for idx, s in enumerate(siblings) if str(s.id) == str(before_item_id)), None)
            moving_index = next((idx for idx, s in enumerate(siblings) if s.id == testcase.id), None)
            if before_index is not None and moving_index is not None:
                moving = siblings.pop(moving_index)
                if moving_index < before_index:
                    before_index -= 1
                siblings.insert(before_index, moving)
                _resequence_testcases(target_id, ordered_ids=[s.id for s in siblings])
            else:
                _resequence_testcases(target_id)
        else:
            _resequence_testcases(target_id)

        if source_folder_id != target_id:
            _resequence_testcases(source_folder_id)

        return JsonResponse({"ok": True, "message": "Test case moved."})

    return JsonResponse({"ok": False, "message": "Invalid move request."}, status=400)


@role_required("Admin", "Tester")
def execution_live_state(request):
    return JsonResponse({"runs": execution_manager.get_state()})


@role_required("Admin", "Tester")
def execution_live_run_pause(request, run_id):
    if request.method != "POST":
        return JsonResponse({"ok": False, "message": "Invalid request method."}, status=405)
    ok, message = execution_manager.toggle_run_pause(run_id)
    return JsonResponse({"ok": ok, "message": message})


@role_required("Admin", "Tester")
def execution_live_run_stop(request, run_id):
    if request.method != "POST":
        return JsonResponse({"ok": False, "message": "Invalid request method."}, status=405)
    ok, message = execution_manager.stop_run(run_id)
    return JsonResponse({"ok": ok, "message": message})


@role_required("Admin", "Tester")
def execution_live_job_pause(request, job_id):
    if request.method != "POST":
        return JsonResponse({"ok": False, "message": "Invalid request method."}, status=405)
    ok, message = execution_manager.toggle_job_pause(job_id)
    return JsonResponse({"ok": ok, "message": message})


@role_required("Admin", "Tester")
def execution_live_job_stop(request, job_id):
    if request.method != "POST":
        return JsonResponse({"ok": False, "message": "Invalid request method."}, status=405)
    ok, message = execution_manager.stop_job(job_id)
    return JsonResponse({"ok": ok, "message": message})


@login_required
def execution_list(request):
    executions = TestExecution.objects.select_related("test_case").all()[:100]
    return render(request, "db/executions/list.html", {"executions": executions})


@login_required
def execution_detail(request, pk):
    execution = get_object_or_404(
        TestExecution.objects.select_related("test_case", "test_case__connection"),
        pk=pk,
    )
    return render(request, "db/executions/detail.html", {"execution": execution})


@login_required
def execution_actual_csv(request, pk):
    execution = get_object_or_404(
        TestExecution.objects.select_related("test_case"),
        pk=pk,
    )

    response = HttpResponse(content_type="text/csv")
    base_name = _download_base_name(execution.test_case.name)
    response["Content-Disposition"] = f'attachment; filename="{base_name}.csv"'

    response.write(_actual_value_to_csv_text(execution.actual_value))
    return response


@login_required
def execution_actual_json(request, pk):
    execution = get_object_or_404(
        TestExecution.objects.select_related("test_case"),
        pk=pk,
    )

    payload = {
        "actual_value": _actual_value_to_csv_text(execution.actual_value),
        "actual_value_format": "csv",
        "executed_at": execution.executed_at.isoformat(),
    }
    response = HttpResponse(
        json.dumps(payload, ensure_ascii=False, indent=2),
        content_type="application/json",
    )
    base_name = _download_base_name(execution.test_case.name)
    response["Content-Disposition"] = f'attachment; filename="{base_name}.json"'
    return response


@login_required
def execution_report_csv(request, pk):
    execution = get_object_or_404(
        TestExecution.objects.select_related("test_case", "test_case__connection"),
        pk=pk,
    )
    conn = execution.test_case.connection

    response = HttpResponse(content_type="text/csv")
    base_name = _download_base_name(execution.test_case.name)
    response["Content-Disposition"] = f'attachment; filename="{base_name}.csv"'

    writer = csv.writer(response)
    writer.writerow(["Execution ID", execution.id])
    writer.writerow(["Executed At", execution.executed_at.isoformat()])
    writer.writerow(["Status", execution.status])
    writer.writerow(["Test Case", execution.test_case.name])
    writer.writerow(["Test Type", execution.test_case.get_test_type_display()])
    writer.writerow(["Actual Value", execution.actual_value])
    writer.writerow(["Details", execution.details])
    writer.writerow([])
    writer.writerow(["Connection Name", conn.name])
    writer.writerow(["Database Type", conn.db_type])
    writer.writerow(["Host", conn.host])
    writer.writerow(["Port", conn.port])
    writer.writerow(["Database Name", conn.database_name])
    writer.writerow(["Service Name", conn.service_name])
    writer.writerow(["Username", conn.username])

    return response


@login_required
def execution_report_doc(request, pk):
    execution = get_object_or_404(
        TestExecution.objects.select_related("test_case", "test_case__connection"),
        pk=pk,
    )
    payload = _execution_report_payload(execution)
    actual_preview = payload["actual_value"]
    if len(actual_preview) > 320:
        actual_preview = actual_preview[:317] + "..."
    status_theme = _status_theme(payload["status"])
    doc_html = [
        "<html><head><meta charset='utf-8'><title>Execution Report</title>",
        "<style>",
        "body { font-family: Segoe UI, Arial, sans-serif; background:#f4f8f7; color:#1f2937; margin:0; padding:24px; position:relative; }",
        ".wm { position:fixed; inset:0; pointer-events:none; z-index:0; display:flex; align-items:center; justify-content:center; font-size:86px; font-weight:800; letter-spacing:0.02em; color:#d9e8e4; transform:rotate(-18deg); }",
        ".wm small { display:block; text-align:center; font-size:34px; font-weight:700; margin-top:6px; color:#e2ece9; }",
        ".page { max-width: 900px; margin: 0 auto; background:#ffffff; border:1px solid #dbe7e3; border-radius:16px; overflow:hidden; }",
        ".head { background: linear-gradient(120deg, #0f766e, #14b8a6); color:#ffffff; padding:18px 22px; }",
        ".head h1 { margin:0; font-size:22px; }",
        ".head p { margin:4px 0 0 0; opacity:0.9; font-size:13px; }",
        ".pill { display:inline-block; padding:4px 10px; border-radius:999px; font-weight:700; font-size:12px; border:1px solid #00000022; }",
        ".grid { display:grid; grid-template-columns: 1fr 1fr; gap:14px; padding:18px 22px; }",
        ".card { border:1px solid #dbe7e3; border-radius:12px; background:#fbfefd; overflow:hidden; }",
        ".card h3 { margin:0; padding:10px 12px; background:#edf7f3; color:#0f4b46; font-size:14px; }",
        ".card table { width:100%; border-collapse:collapse; table-layout:fixed; }",
        ".card td { padding:8px 12px; border-top:1px solid #edf1ef; font-size:12px; vertical-align:top; }",
        ".label { width:34%; color:#475569; font-weight:600; }",
        ".value-wrap { white-space:pre-wrap; word-wrap:break-word; word-break:break-all; overflow-wrap:anywhere; line-height:1.45; }",
        ".wide { grid-column: 1 / -1; }",
        ".mono { font-family: Consolas, Menlo, monospace; white-space: pre-wrap; word-wrap:break-word; word-break:break-all; overflow-wrap:anywhere; background:#f8fafc; border:1px solid #e2e8f0; border-radius:10px; padding:10px; }",
        "</style></head><body>",
        "<div class='wm'>DB TestLab<small>Execution Report</small></div>",
        "<div class='page'>",
        "<div class='head'>",
        f"<h1>Execution Report #{payload['execution_id']}</h1>",
        f"<p>Generated by DB TestLab | {html_escape(payload['executed_at'])}</p>",
        "</div>",
        "<div class='grid'>",
        "<div class='card'>",
        "<h3>Execution Snapshot</h3>",
        "<table>",
        f"<tr><td class='label'>Status</td><td><span class='pill' style='color:{status_theme['fg']}; background:{status_theme['bg']}; border-color:{status_theme['border']};'>{html_escape(payload['status'])}</span></td></tr>",
        f"<tr><td class='label'>Test Case</td><td>{html_escape(payload['test_case'])}</td></tr>",
        f"<tr><td class='label'>Test Type</td><td>{html_escape(payload['test_type'])}</td></tr>",
        f"<tr><td class='label'>Actual Value</td><td><div class='value-wrap'>{html_escape(actual_preview)}</div></td></tr>",
        "</table>",
        "</div>",
        "<div class='card'>",
        "<h3>Connection</h3>",
        "<table>",
        f"<tr><td class='label'>Name</td><td>{html_escape(payload['connection']['name'])}</td></tr>",
        f"<tr><td class='label'>DB Type</td><td>{html_escape(payload['connection']['db_type'])}</td></tr>",
        f"<tr><td class='label'>Host</td><td>{html_escape(payload['connection']['host'])}</td></tr>",
        f"<tr><td class='label'>Port</td><td>{html_escape(payload['connection']['port'])}</td></tr>",
        f"<tr><td class='label'>Database</td><td>{html_escape(payload['connection']['database_name'])}</td></tr>",
        f"<tr><td class='label'>Service</td><td>{html_escape(payload['connection']['service_name'])}</td></tr>",
        f"<tr><td class='label'>Username</td><td>{html_escape(payload['connection']['username'])}</td></tr>",
        "</table>",
        "</div>",
        "<div class='card wide'>",
        "<h3>Details</h3>",
        f"<div class='mono'>{html_escape(payload['details'])}</div>",
        "</div>",
        "<div class='card wide'>",
        "<h3>Query</h3>",
        f"<div class='mono'>{html_escape(payload['query'])}</div>",
        "</div>",
        "</div>",
        "</div></body></html>",
    ]

    response = HttpResponse("".join(doc_html), content_type="application/msword")
    base_name = _download_base_name(execution.test_case.name)
    response["Content-Disposition"] = f'attachment; filename="{base_name}.doc"'
    return response


@login_required
def execution_report_pdf(request, pk):
    execution = get_object_or_404(
        TestExecution.objects.select_related("test_case", "test_case__connection"),
        pk=pk,
    )
    payload = _execution_report_payload(execution)
    pdf_bytes = _modern_pdf_from_payload(payload)

    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    base_name = _download_base_name(execution.test_case.name)
    response["Content-Disposition"] = f'attachment; filename="{base_name}.pdf"'
    return response


@login_required
def execution_export_csv(request):
    executions = TestExecution.objects.select_related("test_case").all().order_by("-executed_at")

    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="execution_results.csv"'

    writer = csv.writer(response)
    writer.writerow(["Test Case", "Status", "Actual Value", "Details", "Executed At"])

    for ex in executions:
        writer.writerow([
            ex.test_case.name,
            ex.status,
            ex.actual_value,
            ex.details,
            ex.executed_at.isoformat(),
        ])

    return response


@login_required
def execution_export_excel(request):
    executions = TestExecution.objects.select_related("test_case").all().order_by("-executed_at")

    wb = Workbook()
    ws = wb.active
    ws.title = "Execution Results"
    ws.append(["Test Case", "Status", "Actual Value", "Details", "Executed At"])

    for ex in executions:
        ws.append([
            ex.test_case.name,
            ex.status,
            ex.actual_value,
            ex.details,
            ex.executed_at.isoformat(),
        ])

    response = HttpResponse(
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    response["Content-Disposition"] = 'attachment; filename="execution_results.xlsx"'
    wb.save(response)
    return response


@admin_required
def admin_user_list(request):
    users = User.objects.all().order_by("username")
    return render(request, "db/admin/users/list.html", {"users": users})


@admin_required
def admin_user_edit(request, pk):
    instance = get_object_or_404(User, pk=pk)
    if request.method == "POST":
        form = AdminUserForm(request.POST, instance=instance)
        if form.is_valid():
            form.save()
            messages.success(request, "User updated successfully.")
            return redirect("db:admin_user_list")
    else:
        form = AdminUserForm(instance=instance)
    return render(
        request,
        "db/admin/users/form.html",
        {
            "form": form,
            "target_user": instance,
        },
    )


@admin_required
def admin_theme_list(request):
    themes = Theme.objects.all().order_by("name")
    current_theme_id = None
    pref = getattr(request.user, "theme_preference", None)
    if pref and pref.theme_id:
        current_theme_id = pref.theme_id
    return render(
        request,
        "db/admin/themes/list.html",
        {
            "themes": themes,
            "current_theme_id": current_theme_id,
        },
    )


@admin_required
def admin_theme_create(request):
    if request.method == "POST":
        form = ThemeForm(request.POST)
        if form.is_valid():
            theme = form.save()
            if theme.is_default:
                Theme.objects.exclude(pk=theme.pk).update(is_default=False)
            messages.success(request, "Theme created successfully.")
            return redirect("db:admin_theme_list")
    else:
        form = ThemeForm()
    return render(request, "db/admin/themes/form.html", {"form": form, "title": "New Theme"})


@admin_required
def admin_theme_edit(request, pk):
    instance = get_object_or_404(Theme, pk=pk)
    if request.method == "POST":
        form = ThemeForm(request.POST, instance=instance)
        if form.is_valid():
            theme = form.save()
            if theme.is_default:
                Theme.objects.exclude(pk=theme.pk).update(is_default=False)
            messages.success(request, "Theme updated successfully.")
            return redirect("db:admin_theme_list")
    else:
        form = ThemeForm(instance=instance)
    return render(request, "db/admin/themes/form.html", {"form": form, "title": "Edit Theme"})


@admin_required
def admin_theme_set_default(request, pk):
    if request.method != "POST":
        messages.error(request, "Invalid request method.")
        return redirect("db:admin_theme_list")

    theme = get_object_or_404(Theme, pk=pk)
    Theme.objects.exclude(pk=theme.pk).update(is_default=False)
    theme.is_default = True
    theme.save(update_fields=["is_default"])
    messages.success(request, f"{theme.name} set as default theme.")
    return redirect("db:admin_theme_list")


@admin_required
def admin_theme_apply_me(request, pk):
    if request.method != "POST":
        messages.error(request, "Invalid request method.")
        return redirect("db:admin_theme_list")

    theme = get_object_or_404(Theme, pk=pk)
    pref, _ = UserThemePreference.objects.get_or_create(user=request.user)
    pref.theme = theme
    pref.save(update_fields=["theme"])
    messages.success(request, f"Applied {theme.name} to your account.")
    return redirect("db:admin_theme_list")


@role_required("Admin", "Tester")
@login_required
def audit_log_list(request):
    """Display audit logs with filtering and search."""
    logs = AuditLog.objects.select_related("user").all()
    
    # Filters
    user_id = (request.GET.get("user") or "").strip()
    action = (request.GET.get("action") or "").strip()
    target_type = (request.GET.get("target_type") or "").strip()
    search = (request.GET.get("search") or "").strip()
    
    if user_id:
        logs = logs.filter(user_id=user_id)
    
    if action:
        logs = logs.filter(action=action)
    
    if target_type:
        logs = logs.filter(target_type=target_type)
    
    if search:
        logs = logs.filter(
            models.Q(target_name__icontains=search)
            | models.Q(details__icontains=search)
            | models.Q(user__username__icontains=search)
        )
    
    # Pagination
    logs = logs.order_by("-created_at")[:500]  # Limit to last 500 logs
    
    # Get unique values for filters
    all_users = User.objects.filter(db_audit_logs__isnull=False).distinct().order_by("username")
    all_actions = AuditLog.Action.choices
    all_target_types = AuditLog.objects.values_list("target_type", flat=True).distinct().order_by("target_type")
    
    return render(
        request,
        "db/admin/audit_logs/list.html",
        {
            "logs": logs,
            "all_users": all_users,
            "all_actions": all_actions,
            "all_target_types": all_target_types,
            "selected_user": user_id,
            "selected_action": action,
            "selected_target_type": target_type,
            "search_query": search,
        },
    )


@role_required("Admin", "Tester")
@login_required
def audit_log_detail(request, pk):
    """Display details of a specific audit log."""
    log = get_object_or_404(AuditLog, pk=pk)
    return render(request, "db/admin/audit_logs/detail.html", {"log": log})

