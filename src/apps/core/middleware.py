import threading
import uuid

# Thread-local storage for request correlation context
_request_context = threading.local()


def get_current_request_id():
    return getattr(_request_context, "request_id", None)


def get_current_user():
    return getattr(_request_context, "user", None)


def get_client_ip():
    return getattr(_request_context, "client_ip", None)


class RequestIDMiddleware:
    """
    Middleware that assigns a unique UUID correlation ID (X-Request-ID) to every HTTP request.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        request.request_id = request_id
        _request_context.request_id = request_id

        # Determine client IP address
        x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
        if x_forwarded_for:
            ip = x_forwarded_for.split(",")[0].strip()
        else:
            ip = request.META.get("REMOTE_ADDR")
        _request_context.client_ip = ip

        try:
            response = self.get_response(request)
        finally:
            # Never leak request context into later work on the same thread.
            _request_context.request_id = None
            _request_context.client_ip = None
        response["X-Request-ID"] = request_id
        return response


class AuditContextMiddleware:
    """
    Middleware that sets the current user in thread-local storage for audit tracking.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if hasattr(request, "user") and request.user.is_authenticated:
            _request_context.user = request.user
        else:
            _request_context.user = None

        try:
            return self.get_response(request)
        finally:
            _request_context.user = None


class SecurityHeadersMiddleware:
    """
    Middleware that enforces baseline security headers per PRD and AGENTS.md §12:
    Content-Security-Policy, Strict-Transport-Security, Permissions-Policy.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if "Content-Security-Policy" not in response:
            response["Content-Security-Policy"] = (
                "default-src 'self'; "
                "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://cdn.jsdelivr.net https://unpkg.com https://cdnjs.cloudflare.com; "
                "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com https://fonts.googleapis.com; "
                "font-src 'self' https://fonts.gstatic.com https://cdnjs.cloudflare.com https://cdn.jsdelivr.net data:; "
                "img-src 'self' data: https:; "
                "connect-src 'self' https://cdn.jsdelivr.net https://unpkg.com;"
            )
        if request.is_secure() and "Strict-Transport-Security" not in response:
            response["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        if "Permissions-Policy" not in response:
            response["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
        return response
