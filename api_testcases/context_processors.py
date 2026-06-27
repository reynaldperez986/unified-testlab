from django.db.utils import OperationalError, ProgrammingError

from .models import ThemeSettings


def app_theme_settings(request):
    """Expose persisted theme settings to all templates."""
    try:
        theme, _ = ThemeSettings.objects.get_or_create(name='default')
    except (OperationalError, ProgrammingError):
        theme = None
    return {
        'app_theme': theme,
    }
