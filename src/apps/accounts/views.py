from django.contrib import messages

from django.contrib.auth import authenticate, login, logout, update_session_auth_hash

from django.contrib.auth.decorators import login_required

from django.contrib.auth.forms import PasswordChangeForm, SetPasswordForm

from django.utils.http import url_has_allowed_host_and_scheme
from django.shortcuts import redirect, render

from django.urls import reverse
from django.views.decorators.cache import never_cache





@never_cache
def portal_login_view(request):

    """

    Renders and processes the ProcureSphere 360 ERP Portal login form.

    Redirects authenticated users straight to the ERP Dashboard (/) or

    forces password change if must_change_password is True.

    """

    if request.user.is_authenticated:

        if getattr(request.user, "must_change_password", False):
            return redirect(reverse("accounts:change_password"))
        role_code = getattr(request.user, "role_code", None) or (
            request.user.role.code if hasattr(request.user, "role") and request.user.role else None
        )
        if role_code == "VENDOR_USER":
            return redirect("/vendor/")

        return redirect("/")



    if request.method == "POST":

        email = (request.POST.get("email") or request.POST.get("username") or "").strip()

        password = request.POST.get("password", "")



        if not email or not password:

            messages.error(request, "Please provide both email and password.")

            return render(request, "pages/login.html", {"email": email})



        # Explicit request=request keyword parameter is required for django-axes compatibility

        user = authenticate(request=request, username=email, password=password)



        if user is not None:

            if not user.is_active:

                messages.error(

                    request, "Your account has been deactivated. Please contact support."

                )

                return render(request, "pages/login.html", {"email": email})



            login(request, user)



            # Record AuditLog for authentication

            try:

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

            except (ImportError, Exception):

                pass



            if getattr(user, "must_change_password", False):
                return redirect(reverse("accounts:change_password"))

            next_url = request.GET.get("next") or request.POST.get("next")
            if next_url and not url_has_allowed_host_and_scheme(
                next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
            ):
                next_url = None  # never redirect off-site (open-redirect protection)
            if not next_url:
                role_code = getattr(user, "role_code", None) or (
                    user.role.code if hasattr(user, "role") and user.role else None
                )
                if role_code == "VENDOR_USER":
                    next_url = "/vendor/"
                else:
                    next_url = "/"

            return redirect(next_url)

        else:

            messages.error(request, "Invalid email or password. Please try again.")



    return render(request, "pages/login.html")





@login_required

def change_password_view(request):

    """

    Renders and processes the password change form.

    Mandatory on first login for users created by Super Admin with must_change_password=True.

    """

    if request.method == "POST":

        form = PasswordChangeForm(request.user, request.POST)

        if form.is_valid():

            user = form.save()

            user.must_change_password = False

            user.save(update_fields=["must_change_password"])

            update_session_auth_hash(request, user)  # Keep user session active



            from apps.audit.models import AuditLog

            from apps.audit.services import create_audit_log_service



            create_audit_log_service(

                actor=user,

                action=AuditLog.ACTION_UPDATE,

                target_model="User",

                target_object_id=str(user.id),

                previous_state={"must_change_password": True},

                new_state={"must_change_password": False, "action": "first_login_password_change"},

                ip_address=request.META.get("REMOTE_ADDR"),

                user_agent=request.META.get("HTTP_USER_AGENT", ""),

            )



            messages.success(

                request,

                "Your password has been changed successfully. Welcome to ProcureSphere 360!",

            )

            return redirect("/")

        else:

            messages.error(request, "Please correct the password errors below.")

    else:

        form = PasswordChangeForm(request.user)



    return render(

        request,

        "accounts/change_password.html",

        {

            "form": form,

            "is_forced": getattr(request.user, "must_change_password", False),

        },

    )





@never_cache
def portal_logout_view(request):

    """

    Logs out the user from the ERP Portal and redirects to /login/.

    """

    user = request.user if request.user.is_authenticated else None

    if user:

        from apps.audit.models import AuditLog

        from apps.audit.services import create_audit_log_service



        create_audit_log_service(

            actor=user,

            action=AuditLog.ACTION_LOGOUT,

            target_model="User",

            target_object_id=str(user.id),

            previous_state={"email": user.email},

        )



    logout(request)

    messages.info(request, "You have been logged out successfully.")

    return redirect("/login/")





@login_required(login_url="/login/")

def password_change_view(request):

    """Lets a user replace their password; mandatory on first login with a temporary password."""

    forced = request.user.must_change_password

    form = SetPasswordForm(request.user, request.POST or None)

    if request.method == "POST":

        current = request.POST.get("current_password", "")

        if not request.user.check_password(current):

            form.add_error(None, "Current (temporary) password is incorrect.")

        elif form.is_valid() and not form.errors:

            user = form.save()

            user.must_change_password = False

            user.save(update_fields=["must_change_password"])

            update_session_auth_hash(request, user)



            from apps.audit.models import AuditLog

            from apps.audit.services import create_audit_log_service



            create_audit_log_service(

                actor=user,

                action=AuditLog.ACTION_UPDATE,

                target_model="User",

                target_object_id=str(user.id),

                previous_state={"must_change_password": forced},

                new_state={"must_change_password": False, "event": "password_changed"},

                ip_address=request.META.get("REMOTE_ADDR"),

                user_agent=request.META.get("HTTP_USER_AGENT", ""),

            )

            messages.success(request, "Password updated.")

            return redirect("/")

    return render(request, "pages/password_change.html", {"form": form, "forced": forced})





# Aliases for route compatibility

login_page_view = portal_login_view

logout_page_view = portal_logout_view

