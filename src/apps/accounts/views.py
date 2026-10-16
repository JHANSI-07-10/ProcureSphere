        import logging
        logging.getLogger(__name__).exception("Authentication audit logging failed")


def portal_login_view(request):
    """Log in to ProcureSphere 360 and enforce first-login password changes."""
    if request.user.is_authenticated:
        if getattr(request.user, "must_change_password", False):
            return redirect(reverse("accounts:change_password"))
        return redirect(_home_for(request.user))

    if request.method == "POST":
        email = (request.POST.get("email") or request.POST.get("username") or "").strip()
        password = request.POST.get("password", "")
        if not email or not password:
            messages.error(request, "Please provide both email and password.")
            return render(request, "pages/login.html", {"email": email})

        user = authenticate(request=request, username=email, password=password)
        if user is not None and user.is_active:
            login(request, user)
            _audit(
                user, "ACTION_LOGIN", request,
                new_state={"email": user.email, "role": getattr(user, "role_code", None)},
            )
            if getattr(user, "must_change_password", False):
                return redirect(reverse("accounts:change_password"))

            next_url = request.POST.get("next") or request.GET.get("next")
            if next_url and url_has_allowed_host_and_scheme(
                next_url,
                allowed_hosts={request.get_host()},
                require_https=request.is_secure(),
            ):
                return redirect(next_url)
            return redirect(_home_for(user))

        if user is not None and not user.is_active:
            messages.error(request, "Your account has been deactivated. Please contact support.")
        else:
            messages.error(request, "Invalid email or password. Please try again.")
        return render(request, "pages/login.html", {"email": email})

    return render(request, "pages/login.html")


@login_required(login_url="/login/")
def change_password_view(request):
    """Change the password using the existing accounts/change_password.html form."""
    forced = getattr(request.user, "must_change_password", False)
    form = PasswordChangeForm(request.user, request.POST or None)
    if request.method == "POST":
        if form.is_valid():
            user = form.save()
            user.must_change_password = False
            user.save(update_fields=["must_change_password"])
            update_session_auth_hash(request, user)
            _audit(
                user, "ACTION_UPDATE", request,
                previous_state={"must_change_password": forced},
                new_state={"must_change_password": False, "action": "first_login_password_change"},
            )
            messages.success(request, "Your password has been changed successfully. Welcome to ProcureSphere 360!")
            return redirect(_home_for(user))
        messages.error(request, "Please correct the password errors below.")
    return render(request, "accounts/change_password.html", {"form": form, "is_forced": forced})


def portal_logout_view(request):
    """Log out and redirect to the portal login page."""
    if request.user.is_authenticated:
        user = request.user
        _audit(user, "ACTION_LOGOUT", request, previous_state={"email": user.email})
    logout(request)
    messages.info(request, "You have been logged out successfully.")
    return redirect("/login/")


@login_required(login_url="/login/")
def password_change_view(request):
    """Compatibility password-change view with current-password verification."""
    forced = getattr(request.user, "must_change_password", False)
    form = SetPasswordForm(request.user, request.POST or None)
    if request.method == "POST":
        current = request.POST.get("current_password", "")
        if not request.user.check_password(current):
            form.add_error(None, "Current (temporary) password is incorrect.")
        elif form.is_valid():
            user = form.save()
            user.must_change_password = False
            user.save(update_fields=["must_change_password"])
            update_session_auth_hash(request, user)
            _audit(
                user, "ACTION_UPDATE", request,
                previous_state={"must_change_password": forced},
                new_state={"must_change_password": False, "event": "password_changed"},
            )
            messages.success(request, "Password updated.")
            return redirect(_home_for(user))
    return render(request, "pages/password_change.html", {"form": form, "forced": forced})


login_page_view = portal_login_view
logout_page_view = portal_logout_view
