from django.shortcuts import redirect

EXEMPT_PREFIXES = ("/static/", "/logout/", "/password-change/", "/health")


class ForcePasswordChangeMiddleware:
    """Sends users holding a temporary password to the change-password page until they set their own."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if (
            user is not None
            and user.is_authenticated
            and getattr(user, "must_change_password", False)
            and not request.path.startswith(EXEMPT_PREFIXES)
        ):
            return redirect("password_change")
        return self.get_response(request)
