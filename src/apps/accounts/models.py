import uuid

from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models


class Role(models.Model):
    """
    System RBAC Role (Super Admin, Requester, Dept Approver, Procurement Exec, etc.)
    """

    SUPER_ADMIN = "SUPER_ADMIN"
    REQUESTER = "REQUESTER"
    DEPT_APPROVER = "DEPT_APPROVER"
    PROC_EXEC = "PROC_EXEC"
    PROC_MGR = "PROC_MGR"
    FINANCE_AP = "FINANCE_AP"
    STORES_RECEIVER = "STORES_RECEIVER"
    LEGAL_MGR = "LEGAL_MGR"
    AUDITOR = "AUDITOR"
    VENDOR_USER = "VENDOR_USER"

    ROLE_CHOICES = [
        (SUPER_ADMIN, "Super Admin"),
        (REQUESTER, "Requester"),
        (DEPT_APPROVER, "Department Approver"),
        (PROC_EXEC, "Procurement Executive"),
        (PROC_MGR, "Procurement Manager"),
        (FINANCE_AP, "Finance / AP Specialist"),
        (STORES_RECEIVER, "Stores / Receiver"),
        (LEGAL_MGR, "Legal / Contract Manager"),
        (AUDITOR, "Compliance Auditor"),
        (VENDOR_USER, "Vendor Portal User"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=50, choices=ROLE_CHOICES, unique=True)
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} ({self.code})"


class UserManager(BaseUserManager):
    """
    Custom user manager for User model where email is the unique identifier.
    """

    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError("The Email field must be set")
        email = self.normalize_email(email)
        user = self.model(email=email, username=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        return self.create_user(email, password, **extra_fields)


class User(AbstractUser):
    """
    Custom User model for ProcureSphere 360.
    Uses UUID primary key and links to primary Role and Department.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField(unique=True)
    phone_number = models.CharField(max_length=20, blank=True)
    role = models.ForeignKey(
        Role, on_delete=models.SET_NULL, null=True, blank=True, related_name="users"
    )

    # Scoping relations
    department = models.ForeignKey(
        "organization.Department",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="users",
    )
    vendor = models.ForeignKey(
        "vendors.Vendor",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="vendor_users",
        help_text="If user belongs to a vendor self-service account",
    )

    must_change_password = models.BooleanField(
        default=False,
        help_text="Set for accounts issued with a temporary password; cleared on first change.",
    )

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    def __str__(self):
        return f"{self.email} [{self.role.code if self.role else 'NO_ROLE'}]"

    @property
    def role_code(self):
        return self.role.code if self.role else None

    @property
    def is_vendor(self):
        return self.role_code == Role.VENDOR_USER
