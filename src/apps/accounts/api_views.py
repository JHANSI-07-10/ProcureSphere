from django.contrib.auth import authenticate, login, logout
from django.middleware.csrf import get_token
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import ensure_csrf_cookie
from rest_framework import mixins, status, views, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from .models import Role, User
from .selectors import get_all_roles, get_all_users
from .serializers import RoleSerializer, UserCreateSerializer, UserSerializer


@method_decorator(ensure_csrf_cookie, name="dispatch")
class SessionLoginView(views.APIView):
    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        email = request.data.get("email")
        password = request.data.get("password")

        if not email or not password:
            return Response(
                {
                    "error": {
                        "code": "VALIDATION_ERROR",
                        "message": "Email and password are required.",
                    }
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Check if client/user is already locked out by Axes
        try:
            from axes.handlers.proxy import AxesProxyHandler

            if not AxesProxyHandler.is_allowed(request, {"username": email}):
                return Response(
                    {
                        "error": {
                            "code": "ACCOUNT_LOCKED",
                            "message": "Too many failed login attempts. Please try again later.",
                            "details": {"retry_after_seconds": 180},
                        }
                    },
                    status=status.HTTP_429_TOO_MANY_REQUESTS,
                )
        except Exception:
            pass

        user = authenticate(request, username=email, password=password)
        if user is None:
            if getattr(request, "axes_locked_out", False):
                return Response(
                    {
                        "error": {
                            "code": "ACCOUNT_LOCKED",
                            "message": "Too many failed login attempts. Account is locked temporarily.",
                            "details": {"retry_after_seconds": 180},
                        }
                    },
                    status=status.HTTP_429_TOO_MANY_REQUESTS,
                )
            try:
                from axes.handlers.proxy import AxesProxyHandler

                if not AxesProxyHandler.is_allowed(request, {"username": email}):
                    return Response(
                        {
                            "error": {
                                "code": "ACCOUNT_LOCKED",
                                "message": "Too many failed login attempts. Account is locked temporarily.",
                                "details": {"retry_after_seconds": 180},
                            }
                        },
                        status=status.HTTP_429_TOO_MANY_REQUESTS,
                    )
            except Exception:
                pass

            return Response(
                {
                    "error": {
                        "code": "AUTHENTICATION_FAILED",
                        "message": "Invalid email or password.",
                    }
                },
                status=status.HTTP_401_UNAUTHORIZED,
            )

        if not user.is_active:
            return Response(
                {"error": {"code": "USER_INACTIVE", "message": "Account is inactive."}},
                status=status.HTTP_403_FORBIDDEN,
            )

        login(request, user)
        csrf_token = get_token(request)
        serializer = UserSerializer(user)

        from apps.audit.models import AuditLog
        from apps.audit.services import create_audit_log_service

        create_audit_log_service(
            actor=user,
            action=AuditLog.ACTION_LOGIN,
            target_model="User",
            target_object_id=str(user.id),
            new_state={
                "email": user.email,
                "role": user.role_code if hasattr(user, "role_code") else None,
            },
            ip_address=request.META.get("REMOTE_ADDR"),
            user_agent=request.META.get("HTTP_USER_AGENT", ""),
        )

        return Response(
            {
                "message": "Login successful.",
                "user": serializer.data,
                "csrf_token": csrf_token,
            },
            status=status.HTTP_200_OK,
        )


class SessionLogoutView(views.APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        user = request.user
        if user and user.is_authenticated:
            from apps.audit.models import AuditLog
            from apps.audit.services import create_audit_log_service

            create_audit_log_service(
                actor=user,
                action=AuditLog.ACTION_LOGOUT,
                target_model="User",
                target_object_id=str(user.id),
                previous_state={"email": user.email},
                ip_address=request.META.get("REMOTE_ADDR"),
                user_agent=request.META.get("HTTP_USER_AGENT", ""),
            )

        logout(request)
        return Response({"message": "Logout successful."}, status=status.HTTP_200_OK)


class UserViewSet(viewsets.ModelViewSet):
    queryset = User.objects.all()
    serializer_class = UserSerializer

    def get_permissions(self):
        from .permissions import IsNotVendorUser, IsSuperAdmin

        if self.action == "me":
            return [IsAuthenticated()]
        if self.action in ["create", "update", "partial_update", "destroy"]:
            return [IsAuthenticated(), IsSuperAdmin()]
        # Listing or retrieving users is restricted to internal users (vendors blocked)
        return [IsAuthenticated(), IsNotVendorUser()]

    def get_queryset(self):
        return get_all_users()

    def get_serializer_class(self):
        if self.action == "create":
            return UserCreateSerializer
        return UserSerializer

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        if instance.id == request.user.id:
            return Response(
                {
                    "error": {
                        "code": "VALIDATION_ERROR",
                        "message": "Users cannot delete their own account.",
                    }
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        return super().destroy(request, *args, **kwargs)

    @action(detail=False, methods=["get"], permission_classes=[IsAuthenticated])
    def me(self, request):
        serializer = self.get_serializer(request.user)
        return Response(serializer.data)


class RoleViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    queryset = Role.objects.all()
    serializer_class = RoleSerializer

    def get_permissions(self):
        from .permissions import IsNotVendorUser

        return [IsAuthenticated(), IsNotVendorUser()]

    def get_queryset(self):
        return get_all_roles()
