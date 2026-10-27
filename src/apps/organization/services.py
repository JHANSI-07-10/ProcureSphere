from typing import Optional, Tuple

from django.db import transaction

from apps.accounts.models import Role, User
from apps.accounts.password_utils import generate_secure_password
from apps.audit.models import AuditLog
from apps.notifications.email_service import send_user_welcome_email

from .models import CostCenter, Department, FiscalPeriod, Organization


@transaction.atomic
def create_organization_service(
    *, name: str, code: str, tax_identifier: str = "", address: str = "", is_active: bool = True
) -> Organization:
    """
    Service layer function to create an Organization entity inside a database transaction.
    """
    org = Organization.objects.create(
        name=name.strip(),
        code=code.strip().upper(),
        tax_identifier=tax_identifier.strip(),
        address=address.strip(),
        is_active=is_active,
    )
    return org


@transaction.atomic
def create_organization_with_admin_service(
    *,
    name: str,
    code: str,
    tax_identifier: str = "",
    address: str = "",
    is_active: bool = True,
    admin_email: Optional[str] = None,
    admin_first_name: str = "",
    admin_last_name: str = "",
    admin_password: Optional[str] = None,
    send_welcome_email_flag: bool = True,
    request=None,
    actor=None,
) -> Tuple[Organization, Optional[User], Optional[str]]:
    """
    Creates an Organization and provisions its initial Organization Administrator account.
    Returns: (organization, admin_user, raw_password)
    """
    org = create_organization_service(
        name=name,
        code=code,
        tax_identifier=tax_identifier,
        address=address,
        is_active=is_active,
    )

    admin_user = None
    raw_password = None

    if admin_email and admin_email.strip():
        norm_email = admin_email.strip().lower()
        role, _ = Role.objects.get_or_create(
            code=Role.SUPER_ADMIN,
            defaults={"name": "Super Admin", "description": "System and Organization Administrator"},
        )

        raw_password = admin_password.strip() if admin_password and admin_password.strip() else generate_secure_password()

        admin_user = User.objects.filter(email__iexact=norm_email).first()
        is_new = False
        if not admin_user:
            is_new = True
            admin_user = User(
                email=norm_email,
                username=norm_email,
                first_name=admin_first_name.strip(),
                last_name=admin_last_name.strip(),
                role=role,
                organization=org,
                is_staff=True,
                is_active=True,
                must_change_password=True,
            )
        else:
            if admin_first_name:
                admin_user.first_name = admin_first_name.strip()
            if admin_last_name:
                admin_user.last_name = admin_last_name.strip()
            if not admin_user.organization_id:
                admin_user.organization = org
            if not admin_user.role_id:
                admin_user.role = role
            admin_user.is_staff = True

        admin_user.set_password(raw_password)
        admin_user.save()

        org.admin_user = admin_user
        org.save(update_fields=["admin_user"])

        # Audit log for user creation
        from apps.audit.services import create_audit_log_service

        effective_actor = actor or (request.user if request and getattr(request, "user", None) and request.user.is_authenticated else None)

        create_audit_log_service(
            actor=effective_actor,
            action=AuditLog.ACTION_CREATE if is_new else AuditLog.ACTION_UPDATE,
            target_model="User",
            target_object_id=str(admin_user.id),
            previous_state=None,
            new_state={
                "email": admin_user.email,
                "role": admin_user.role.code if admin_user.role else None,
                "organization": org.code,
                "is_active": admin_user.is_active,
            },
            ip_address=request.META.get("REMOTE_ADDR") if request else None,
            user_agent=request.META.get("HTTP_USER_AGENT", "") if request else "",
        )

        if send_welcome_email_flag and raw_password:
            status, error = send_user_welcome_email(
                user=admin_user,
                temporary_password=raw_password,
                request=request,
            )
            org._welcome_email_status = status
            org._welcome_email_error = error

    # Audit log for Organization creation
    from apps.audit.services import create_audit_log_service

    effective_actor = actor or (request.user if request and getattr(request, "user", None) and request.user.is_authenticated else None)

    create_audit_log_service(
        actor=effective_actor,
        action=AuditLog.ACTION_CREATE,
        target_model="Organization",
        target_object_id=str(org.id),
        previous_state=None,
        new_state={
            "name": org.name,
            "code": org.code,
            "tax_identifier": org.tax_identifier,
            "admin_user": admin_user.email if admin_user else None,
            "is_active": org.is_active,
        },
        ip_address=request.META.get("REMOTE_ADDR") if request else None,
        user_agent=request.META.get("HTTP_USER_AGENT", "") if request else "",
    )

    return org, admin_user, raw_password


@transaction.atomic
def create_department_service(
    *, organization: Organization, name: str, code: str, description: str = ""
) -> Department:
    """
    Service layer function to create a Department inside an Organization.
    """
    department = Department.objects.create(
        organization=organization,
        name=name,
        code=code.upper(),
        description=description,
    )
    return department


@transaction.atomic
def create_cost_center_service(
    *, department: Department, code: str, name: str, manager=None
) -> CostCenter:
    """
    Service layer function to create a Cost Center linked to a Department.
    """
    cost_center = CostCenter.objects.create(
        department=department,
        code=code.upper(),
        name=name,
        manager=manager,
    )
    return cost_center


@transaction.atomic
def create_fiscal_period_service(
    *, organization: Organization, year: int, period_number: int, name: str, start_date, end_date
) -> FiscalPeriod:
    """
    Service layer function to define a Fiscal Period.
    """
    period = FiscalPeriod.objects.create(
        organization=organization,
        year=year,
        period_number=period_number,
        name=name,
        start_date=start_date,
        end_date=end_date,
    )
    return period
