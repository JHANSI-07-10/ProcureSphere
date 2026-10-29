from django.db import models

from apps.core.models import TimeStampedModel


class ExportJob(TimeStampedModel):
    FORMAT_CSV = "CSV"
    FORMAT_XLSX = "XLSX"
    FORMAT_PDF = "PDF"

    FORMAT_CHOICES = [
        (FORMAT_CSV, "CSV"),
        (FORMAT_XLSX, "Excel (XLSX)"),
        (FORMAT_PDF, "PDF Document"),
    ]

    STATUS_PENDING = "PENDING"
    STATUS_PROCESSING = "PROCESSING"
    STATUS_COMPLETED = "COMPLETED"
    STATUS_FAILED = "FAILED"

    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_PROCESSING, "Processing"),
        (STATUS_COMPLETED, "Completed"),
        (STATUS_FAILED, "Failed"),
    ]

    report_type = models.CharField(max_length=100)
    export_format = models.CharField(max_length=10, choices=FORMAT_CHOICES, default=FORMAT_CSV)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)

    requested_by = models.ForeignKey(
        "accounts.User", on_delete=models.PROTECT, related_name="export_jobs"
    )
    result_file = models.FileField(upload_to="report_exports/%Y/%m/", null=True, blank=True)
    file_hash = models.CharField(max_length=64, blank=True, help_text="SHA-256 hex digest of result_file")
    error_message = models.TextField(blank=True)

    def __str__(self):
        return f"Export {self.report_type} ({self.export_format}) - {self.status}"
