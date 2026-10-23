import os
import re
import zipfile

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

ALLOWED_EXTENSIONS = {"pdf", "png", "jpg", "jpeg", "doc", "docx", "xlsx", "csv"}
PR_ATTACHMENT_EXTENSIONS = {"pdf", "doc", "docx"}
MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024  # 10MB

# Indian Tax Identifier Validation Patterns
PAN_REGEX = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]{1}$")
GSTIN_REGEX = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$")


def normalize_tax_identifier(val) -> str:
    """Trim whitespace and uppercase tax identifier strings."""
    if not val:
        return ""
    return str(val).strip().upper()


def is_valid_pan(pan) -> bool:
    """Returns True if normalized string matches PAN regex."""
    if not pan:
        return False
    norm = normalize_tax_identifier(pan)
    return bool(PAN_REGEX.match(norm))


def is_valid_gstin(gstin) -> bool:
    """Returns True if normalized string matches GSTIN regex."""
    if not gstin:
        return False
    norm = normalize_tax_identifier(gstin)
    return bool(GSTIN_REGEX.match(norm))


def extract_pan_from_gstin(gstin) -> str | None:
    """Extract embedded 10-character PAN from valid GSTIN (characters 3-12 / index 2-12)."""
    if not gstin:
        return None
    norm = normalize_tax_identifier(gstin)
    if is_valid_gstin(norm):
        return norm[2:12]
    return None


def validate_pan_number(value: str) -> str:
    """
    Validates PAN format: ^[A-Z]{5}[0-9]{4}[A-Z]{1}$.
    Returns normalized PAN string.
    Raises ValidationError if invalid non-empty value provided.
    """
    if not value or not str(value).strip():
        return ""
    norm = normalize_tax_identifier(value)
    if not PAN_REGEX.match(norm):
        raise ValidationError(
            _("Invalid PAN format. PAN must be 10 characters: 5 uppercase letters, 4 digits, 1 uppercase letter (e.g. ABCDE1234F).")
        )
    return norm


def validate_gstin(value: str) -> str:
    """
    Validates GSTIN format: ^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$.
    Returns normalized GSTIN string.
    Raises ValidationError if invalid non-empty value provided.
    """
    if not value or not str(value).strip():
        return ""
    norm = normalize_tax_identifier(value)
    if not GSTIN_REGEX.match(norm):
        raise ValidationError(
            _("Invalid GSTIN format. GSTIN must be 15 alphanumeric characters (e.g. 29ABCDE1234F1Z5).")
        )
    return norm


def validate_pan_gstin_consistency(pan: str, gstin: str) -> None:
    """
    When both PAN and GSTIN are provided, verify that the PAN embedded
    in the GSTIN (characters 3-12) matches the separate PAN field.
    """
    norm_pan = normalize_tax_identifier(pan)
    norm_gstin = normalize_tax_identifier(gstin)
    if norm_pan and norm_gstin and is_valid_gstin(norm_gstin):
        embedded_pan = norm_gstin[2:12]
        if norm_pan != embedded_pan:
            raise ValidationError(
                _(
                    f"GSTIN '{norm_gstin}' contains embedded PAN '{embedded_pan}' "
                    f"which does not match the separate PAN '{norm_pan}'."
                )
            )



def check_upload_rate_limit(key: str, max_requests: int = 10, window_seconds: int = 60):
    """
    Validates upload rate limiting using Django's caching backend.
    Tracks number of upload attempts per key within the time window.
    Zero external dependencies required.
    """
    cache_key = f"upload_ratelimit_{key}"
    try:
        current_count = cache.get(cache_key)
        if current_count is None:
            cache.set(cache_key, 1, timeout=window_seconds)
        elif current_count >= max_requests:
            raise ValidationError(
                _("Upload rate limit exceeded. Please wait a moment before uploading again.")
            )
        else:
            try:
                cache.incr(cache_key)
            except Exception:
                cache.set(cache_key, current_count + 1, timeout=window_seconds)
    except ValidationError:
        raise
    except Exception:
        # If cache backend is unavailable, fail open to avoid blocking legitimate operations
        pass


def validate_file_upload(file):
    """
    Validates uploaded file size, extension, and binary content signature.
    Enforces:
    1. Rejects missing and zero-byte files.
    2. Enforces maximum file size limit (10MB).
    3. Permitted extension whitelist.
    4. Magic-byte / content signature validation corresponding to the extension.
    5. Structural verification for ZIP-based formats (DOCX, XLSX).
    6. Text & null-byte safety verification for CSV.
    7. File pointer preservation before and after inspection.
    """
    if not file:
        raise ValidationError(_("No file was provided."))

    # 1. Non-empty check
    file_size = getattr(file, "size", None)
    if file_size is None or file_size == 0:
        raise ValidationError(_("Uploaded file is empty. Zero-byte files are not permitted."))

    # 2. Maximum file size check
    if file_size > MAX_FILE_SIZE_BYTES:
        raise ValidationError(_("File size exceeds maximum allowed limit of 10MB."))

    # 3. Extension check
    file_name = getattr(file, "name", "")
    if not file_name or "." not in file_name:
        raise ValidationError(_("File must have a valid extension."))

    ext = os.path.splitext(file_name)[1][1:].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ValidationError(
            _(
                f"File extension '.{ext}' is not permitted. Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}"
            )
        )

    # 3b. MIME type check if content_type is provided (VEN-07)
    content_type = getattr(file, "content_type", None)
    if content_type:
        raw_mime = content_type.split(";")[0].strip().lower()
        generic_fallbacks = {"application/octet-stream", "text/plain"}
        allowed_mimes = {
            "pdf": {"application/pdf"},
            "png": {"image/png"},
            "jpg": {"image/jpeg", "image/pjpeg"},
            "jpeg": {"image/jpeg", "image/pjpeg"},
            "doc": {"application/msword", "application/x-msword"},
            "docx": {
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "application/zip",
            },
            "xlsx": {
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "application/zip",
            },
            "csv": {"text/csv", "application/csv", "application/vnd.ms-excel"},
        }.get(ext, set())
        valid_mimes = allowed_mimes | generic_fallbacks
        if allowed_mimes and raw_mime not in valid_mimes:
            raise ValidationError(
                _(f"Invalid MIME type '{content_type}' for .{ext} file.")
            )

    # 4. Binary signature & content validation (with pointer preservation)
    initial_pos = None
    if hasattr(file, "tell"):
        try:
            initial_pos = file.tell()
        except (AttributeError, OSError):
            initial_pos = None

    try:
        if ext == "pdf":
            header = file.read(8)
            if not header.startswith(b"%PDF"):
                raise ValidationError(
                    _("Invalid file content for .pdf. The file is not a valid PDF document.")
                )

        elif ext == "png":
            header = file.read(8)
            if not header.startswith(b"\x89PNG\r\n\x1a\n"):
                raise ValidationError(
                    _("Invalid file content for .png. The file is not a valid PNG image.")
                )

        elif ext in ("jpg", "jpeg"):
            header = file.read(3)
            if not header.startswith(b"\xff\xd8\xff"):
                raise ValidationError(
                    _("Invalid file content for JPEG. The file is not a valid JPEG image.")
                )

        elif ext == "doc":
            header = file.read(8)
            if not header.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
                raise ValidationError(
                    _("Invalid file content for .doc. The file is not a valid Word document.")
                )

        elif ext in ("docx", "xlsx"):
            # Check standard PK zip header first
            header = file.read(4)
            if not header.startswith(b"PK\x03\x04"):
                raise ValidationError(
                    _(
                        f"Invalid file content for .{ext}. The file does not have a valid ZIP/OpenXML container."
                    )
                )
            # Safely validate OpenXML ZIP directory structure without extracting contents
            if hasattr(file, "seek"):
                file.seek(0)
            try:
                with zipfile.ZipFile(file, "r") as zf:
                    names = zf.namelist()
                    if "[Content_Types].xml" not in names:
                        raise ValidationError(
                            _(
                                f"Invalid file content for .{ext}. Missing OpenXML content structure."
                            )
                        )
                    if ext == "docx" and not any(n.startswith("word/") for n in names):
                        raise ValidationError(
                            _("Invalid file content for .docx. Missing Word document structure.")
                        )
                    if ext == "xlsx" and not any(n.startswith("xl/") for n in names):
                        raise ValidationError(
                            _("Invalid file content for .xlsx. Missing Excel workbook structure.")
                        )
            except zipfile.BadZipFile:
                raise ValidationError(
                    _(f"Invalid file content for .{ext}. The file is corrupt or not a valid archive.")
                )

        elif ext == "csv":
            # For CSV, read small chunk (4KB) to avoid memory overload
            sample = file.read(4096)
            if not sample:
                raise ValidationError(_("CSV file is empty."))
            # Reject known binary executable headers first
            binary_headers = (
                b"MZ",                     # DOS / PE executable
                b"%PDF-",                  # PDF
                b"PK\x03\x04",             # ZIP / OpenXML
                b"\x7fELF",                # ELF executable
                b"\x89PNG",                # PNG
                b"\xff\xd8\xff",           # JPEG
                b"\xd0\xcf\x11\xe0",       # OLECF (doc/xls)
            )
            if any(sample.startswith(sig) for sig in binary_headers):
                raise ValidationError(
                    _("Invalid file content for .csv. Binary file disguised with .csv extension.")
                )
            # Reject null bytes (common indicator of binary payload)
            if b"\x00" in sample:
                raise ValidationError(
                    _(
                        "Invalid file content for .csv. Binary content and null bytes are not permitted."
                    )
                )
            # Ensure text is decodable as utf-8 or latin-1
            try:
                sample.decode("utf-8")
            except UnicodeDecodeError:
                try:
                    sample.decode("latin-1")
                except Exception:
                    raise ValidationError(
                        _("Invalid file content for .csv. File contains non-text binary data.")
                    )

    finally:
        if hasattr(file, "seek") and initial_pos is not None:
            try:
                file.seek(initial_pos)
            except (AttributeError, OSError):
                pass

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
