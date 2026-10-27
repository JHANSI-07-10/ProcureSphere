from django.apps import AppConfig


def _patch_template_context_copy():
    """Django 4.2 context copying breaks admin forms on Python 3.14."""
    from django.template.context import BaseContext

    if getattr(BaseContext.__copy__, "_procuresphere_patch", False):
        return

    def __copy__(self):
        duplicate = object.__new__(self.__class__)
        duplicate.__dict__.update(self.__dict__)
        duplicate.dicts = self.dicts[:]
        return duplicate

    __copy__._procuresphere_patch = True
    BaseContext.__copy__ = __copy__


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.core"
    verbose_name = "Core Framework Utilities"

    def ready(self):
        _patch_template_context_copy()
