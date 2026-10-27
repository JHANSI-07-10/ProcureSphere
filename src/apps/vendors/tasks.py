from celery import shared_task

from .services import scan_vendor_document_expirations_service


@shared_task(name="apps.vendors.tasks.scan_vendor_document_expirations_task")
def scan_vendor_document_expirations_task():
    """
    Periodic Celery Beat task scanning vendor KYC document expirations.
    - Flags expired documents
    - Suspends event eligibility for vendors with expired mandatory documents
    - Emits idempotent renewal notifications/reminders at 30, 15, and 7 days
    """
    summary = scan_vendor_document_expirations_service()
    return (
        f"KYC Document Scan completed: "
        f"{summary['expired_documents']} expired, "
        f"{summary['suspended_vendors']} vendors suspended/ineligible, "
        f"{summary['reminders_sent']} reminders sent."
    )
