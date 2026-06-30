app_name = 'api'

from django.urls import path
from . import views

urlpatterns = [
    # Auth
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),

    # Dashboard
    path('', views.dashboard, name='dashboard'),
    path('dashboard/', views.dashboard, name='dashboard'),

    # Environments
    path('environments/', views.environment_list, name='environment_list'),
    path('environments/create/', views.environment_create, name='environment_create'),
    path('environments/<int:pk>/', views.environment_detail, name='environment_detail'),
    path('environments/<int:pk>/edit/', views.environment_edit, name='environment_edit'),
    path('environments/<int:pk>/delete/', views.environment_delete, name='environment_delete'),
    path('api/environments/<int:pk>/oauth2/new-token/', views.environment_get_new_access_token, name='environment_get_new_access_token'),
    path('api/environments/<int:pk>/test-connection/', views.environment_test_connection, name='environment_test_connection'),

    # Test Cases
    path('testcases/', views.testcase_list, name='testcase_list'),
    path('testcases/global-data/', views.global_test_data_page, name='global_test_data_page'),
    path('testcases/projects/create/', views.project_create, name='project_create'),
    path('testcases/projects/<int:pk>/edit/', views.project_edit, name='project_edit'),
    path('testcases/projects/<int:pk>/delete/', views.project_delete, name='project_delete'),
    path('testcases/projects/<int:pk>/duplicate/', views.project_duplicate, name='project_duplicate'),
    path('testcases/projects/ungrouped/delete/', views.ungrouped_delete, name='ungrouped_delete'),
    path('testcases/create/', views.testcase_create, name='testcase_create'),
    path('testcases/<int:pk>/', views.testcase_detail, name='testcase_detail'),
    path('testcases/<int:pk>/download/requests/', views.testcase_download_requests_py, name='testcase_download_requests_py'),
    path('testcases/<int:pk>/download/playwright/', views.testcase_download_playwright_py, name='testcase_download_playwright_py'),
    path('testcases/<int:pk>/run/<str:snippet_type>/', views.testcase_run_generated_py, name='testcase_run_generated_py'),
    path('api/testcases/<int:pk>/recent-executions/', views.testcase_recent_executions, name='testcase_recent_executions'),
    path('api/testcases/<int:pk>/transformed-response/', views.testcase_transformed_response, name='testcase_transformed_response'),
    path('api/testcases/global-field-values/', views.api_global_field_values, name='api_global_field_values'),
    path('testcases/<int:pk>/edit/', views.testcase_edit, name='testcase_edit'),
    path('testcases/<int:pk>/delete/', views.testcase_delete, name='testcase_delete'),
    path('testcases/<int:pk>/duplicate/', views.testcase_duplicate, name='testcase_duplicate'),
    path('api/testcases/bulk-duplicate/', views.bulk_duplicate_testcases, name='bulk_duplicate_testcases'),
    path('api/testcases/bulk-execute/', views.bulk_execute, name='bulk_execute'),
    path('api/testcases/latest-results/', views.testcase_latest_results, name='testcase_latest_results'),
    path('api/reorder/projects/', views.reorder_projects, name='reorder_projects'),
    path('api/reorder/testcases/', views.reorder_testcases, name='reorder_testcases'),

    # Execution
    path('api/execute/', views.execute_test, name='execute_test'),

    # History
    path('executions/', views.execution_history, name='execution_history'),
    path('executions/<int:pk>/', views.execution_detail, name='execution_detail'),
    path('executions/<int:pk>/download/csv/', views.execution_download_csv, name='execution_download_csv'),
    path('executions/<int:pk>/download/docx/', views.execution_download_docx, name='execution_download_docx'),
    path('executions/<int:pk>/download/pdf/', views.execution_download_pdf, name='execution_download_pdf'),
    path('executions/<int:pk>/response/download/json/', views.execution_download_response_json, name='execution_download_response_json'),
    path('executions/<int:pk>/response/download/csv/', views.execution_download_response_csv, name='execution_download_response_csv'),
    path('executions/<int:pk>/request-headers/download/json/', views.execution_download_request_headers_json, name='execution_download_request_headers_json'),
    path('executions/<int:pk>/request-headers/download/csv/', views.execution_download_request_headers_csv, name='execution_download_request_headers_csv'),
    path('executions/<int:pk>/request-body/download/json/', views.execution_download_request_body_json, name='execution_download_request_body_json'),
    path('executions/<int:pk>/request-body/download/csv/', views.execution_download_request_body_csv, name='execution_download_request_body_csv'),
    path('executions/export/csv/', views.export_executions_csv, name='export_executions_csv'),

    # User Management
    path('users/', views.user_list, name='user_list'),
    path('users/create/', views.user_create, name='user_create'),
    path('users/<int:pk>/edit/', views.user_edit, name='user_edit'),
    path('users/<int:pk>/deactivate/', views.user_deactivate, name='user_deactivate'),
    path('users/<int:pk>/delete/', views.user_delete, name='user_delete'),

    # Audit Logs
    path('audit-logs/', views.audit_log_list, name='audit_log_list'),

    # Modules
    path('modules/', views.module_list, name='module_list'),
    path('modules/upload/', views.module_upload, name='module_upload'),
    path('modules/<int:pk>/', views.module_detail, name='module_detail'),
    path('modules/<int:pk>/delete/', views.module_delete, name='module_delete'),
    path('modules/<int:pk>/base-path/', views.module_update_base_path, name='module_update_base_path'),
    path('modules/<int:pk>/oauth/', views.module_update_oauth, name='module_update_oauth'),
    path('modules/endpoints/<int:pk>/edit/', views.endpoint_edit, name='endpoint_edit'),
    path('modules/endpoints/<int:pk>/upload-payload/', views.endpoint_upload_payload, name='endpoint_upload_payload'),
    path('api/modules/', views.api_modules_list, name='api_modules_list'),
    path('api/modules/<int:pk>/endpoints/', views.api_module_endpoints, name='api_module_endpoints'),
    path('api/modules/<int:pk>/auth/', views.api_module_auth, name='api_module_auth'),
    path('api/environments/<int:pk>/', views.api_environment_details, name='api_environment_details'),

    # Theme
    path('theme/', views.theme_settings, name='theme_settings'),
]

