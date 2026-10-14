import os

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

ALLOWED_EXTENSIONS = {"pdf", "png", "jpg", "jpeg", "doc", "docx", "xlsx", "csv"}
PR_ATTACHMENT_EXTENSIONS = {"pdf", "doc", "docx"}
MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024  # 10MB


def validate_file_upload(file):
    """
    Validates uploaded file size and extension.
    """
    if file.size > MAX_FILE_SIZE_BYTES:
        raise ValidationError(_("File size exceeds maximum allowed limit of 10MB."))

    ext = os.path.splitext(file.name)[1][1:].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ValidationError(
            _(
                f"File extension '.{ext}' is not permitted. Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}"
            )
        )
    return file


def validate_pr_attachment(file):
    """
    Validates PR attachments: only PDF and Word documents (DOC/DOCX) are allowed.
    Enforces extension check, max size (10MB), and magic bytes binary signature check.
    """
    if not file:
        raise ValidationError(_("Invalid file format."))

    if file.size > MAX_FILE_SIZE_BYTES:
        raise ValidationError(_("File size exceeds the maximum allowed limit of 10MB."))

    ext = os.path.splitext(file.name)[1][1:].lower()
    if ext not in PR_ATTACHMENT_EXTENSIONS:
        raise ValidationError(_("Invalid file format."))

    # Validate Magic Bytes binary signature
    initial_pos = None
    if hasattr(file, "tell"):
        try:
            initial_pos = file.tell()
        except (AttributeError, OSError):
            initial_pos = None

    try:
        header = file.read(8)
    except Exception:
        raise ValidationError(_("Invalid file format."))
    finally:
        if hasattr(file, "seek") and initial_pos is not None:
            try:
                file.seek(initial_pos)
            except (AttributeError, OSError):
                pass

    if not header:
        raise ValidationError(_("Invalid file format."))

    if ext == "pdf":
        if not header.startswith(b"%PDF-"):
            raise ValidationError(_("Invalid file format."))
    elif ext == "doc":
        if not header.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
            raise ValidationError(_("Invalid file format."))
    elif ext == "docx":
        if not header.startswith(b"PK\x03\x04"):
            raise ValidationError(_("Invalid file format."))
    else:
        raise ValidationError(_("Invalid file format."))

    return file
