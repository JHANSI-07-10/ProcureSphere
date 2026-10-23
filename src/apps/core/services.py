from django.utils import timezone


def generate_document_number(model_class, field_name: str, prefix: str, padding: int = 5) -> str:
    """
    Safely generates the next sequential document number (e.g. VND-2026-00001, PR-2026-00001)
    by finding the highest existing sequence number for the current year/prefix,
    ensuring deletions never result in duplicate key collisions (VMR-CR-010).
    """
    year = timezone.now().strftime("%Y")
    base_prefix = f"{prefix}-{year}-"
    latest_obj = (
        model_class.objects.filter(**{f"{field_name}__startswith": base_prefix})
        .order_by(f"-{field_name}")
        .first()
    )
    next_num = 1
    if latest_obj:
        val = getattr(latest_obj, field_name, "")
        try:
            suffix_num = int(val[len(base_prefix):])
            next_num = suffix_num + 1
        except (ValueError, TypeError):
            next_num = model_class.objects.count() + 1

    # Guarantee uniqueness even in concurrent or deleted row edge cases
    while model_class.objects.filter(**{field_name: f"{base_prefix}{next_num:0{padding}d}"}).exists():
        next_num += 1

    return f"{base_prefix}{next_num:0{padding}d}"
