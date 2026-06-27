from .views import get_config


def app_features(request):
    """Inject feature-flag booleans into every template context."""
    return {
        "feat_history":        get_config("features.history_enabled",         "true") == "true",
        "feat_licensing":      get_config("features.licensing_enabled",       "true") == "true",
        "feat_users":          get_config("features.user_management_enabled",  "true") == "true",
        "feat_bulk_replay":    get_config("features.bulk_replay_enabled",      "true") == "true",
    }
