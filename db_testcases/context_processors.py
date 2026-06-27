from django.db.utils import OperationalError, ProgrammingError

from .models import Theme


def app_navigation(request):
    theme = None
    try:
        if request.user.is_authenticated:
            pref = getattr(request.user, "theme_preference", None)
            if pref and pref.theme:
                theme = pref.theme

        if theme is None:
            theme = Theme.objects.filter(is_default=True).first()
    except (OperationalError, ProgrammingError):
        theme = None

    if theme is None:
        theme = Theme(
            name="Default",
            primary_color="#0f766e",
            accent_color="#f59e0b",
            background_color="#f3f7f5",
            surface_color="#ffffff",
            text_color="#122322",
            border_color="#d7e2df",
            sidebar_start_color="#0b2d2a",
            sidebar_end_color="#133734",
        )

    is_admin = request.user.is_authenticated and (
        request.user.is_superuser or request.user.groups.filter(name="Admin").exists()
    )

    return {
        "app_theme": theme,
        "is_admin": is_admin,
    }
