from celery import shared_task


@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def run_export_job_task(self, export_job_id: int):
    from apps.reports.services import generate_export_job_service

    try:
        generate_export_job_service(export_job_id)
    except Exception as exc:
        raise self.retry(exc=exc)
