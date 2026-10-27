import getpass
import secrets
import string

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from apps.audit.models import AuditLog
from apps.audit.services import create_audit_log_service


class Command(BaseCommand):
    help = "Securely resets a vendor or user account password and clears any active login lockouts."

    def add_arguments(self, parser):
        parser.add_argument(
            "--email",
            type=str,
            required=True,
            help="Email address of the user whose password should be reset.",
        )
        parser.add_argument(
            "--password",
            type=str,
            required=False,
            default=None,
            help="Optional new password. If omitted, prompts securely via console.",
        )
        parser.add_argument(
            "--generate",
            action="store_true",
            help="Generate a cryptographically secure temporary password.",
        )
        parser.add_argument(
            "--force-change",
            action="store_true",
            help="Flag user to change password upon next login (must_change_password=True).",
        )
        parser.add_argument(
            "--activate",
            action="store_true",
            help="Reactivate account if currently deactivated.",
        )

    def handle(self, *args, **options):
        User = get_user_model()
        email = User.objects.normalize_email(options["email"].strip())

        try:
            user = User.objects.get(email__iexact=email)
        except User.DoesNotExist:
            raise CommandError(f"User with email '{email}' does not exist in the database.")

        if not user.is_active and not options["activate"]:
            raise CommandError(
                f"User account '{email}' is currently inactive. Pass --activate to reactivate and reset."
            )

        password = options["password"]
        if options["generate"]:
            alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
            password = "".join(secrets.choice(alphabet) for _ in range(16))
            self.stdout.write(self.style.WARNING(f"Generated secure temporary password for {email}: {password}"))
        elif not password:
            password = getpass.getpass(prompt=f"Enter new password for {email}: ")
            confirm = getpass.getpass(prompt="Confirm new password: ")
            if password != confirm:
                raise CommandError("Passwords do not match.")

        try:
            validate_password(password, user=user)
        except ValidationError as err:
            raise CommandError("; ".join(err.messages))

        # Securely hash password using Django's password hasher (never stored in plaintext)
        user.set_password(password)
        if options["activate"]:
            user.is_active = True
        user.must_change_password = options["force_change"]
        user.save()

        # Clear Axes lockouts and cached attempt counters
        try:
            from axes.models import AccessAttempt
            deleted_count, _ = AccessAttempt.objects.filter(username__iexact=email).delete()
            cache.clear()
            lockout_cleared = deleted_count > 0
        except Exception:
            lockout_cleared = False

        # Record audit log entry
        try:
            create_audit_log_service(
                actor=user,
                action=AuditLog.ACTION_UPDATE,
                target_model="User",
                target_object_id=str(user.id),
                new_state={
                    "event": "admin_password_reset",
                    "email": user.email,
                    "must_change_password": user.must_change_password,
                },
            )
        except Exception:
            pass

        self.stdout.write(
            self.style.SUCCESS(
                f"Successfully updated password hash for '{user.email}'. "
                f"Active: {user.is_active}, must_change_password: {user.must_change_password}."
                + (" Cleared active Axes lockout." if lockout_cleared else "")
            )
        )
