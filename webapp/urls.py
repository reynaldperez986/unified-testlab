from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path, include, re_path
from django.views.generic.base import RedirectView
from recorder import views as recorder_views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("login/", auth_views.LoginView.as_view(template_name="recorder/login.html"), name="login"),
    path("logout/", recorder_views.logout_view, name="logout"),
    path("api-lab/", include(("api_testcases.urls", "api"), namespace="api")),
    path("db-lab/", include(("db_testcases.urls", "db"), namespace="db")),
    # Backward compatibility for legacy API execution links.
    path("executions/", RedirectView.as_view(url="/api-lab/executions/", permanent=False)),
    re_path(r"^executions/(?P<tail>.*)$", RedirectView.as_view(url="/api-lab/executions/%(tail)s", permanent=False)),
    path("", include("recorder.urls")),
]
