from apps.accounts.models import Role


def layout_context(request):
    """
    Provides dynamic base_layout template name exclusively for Legal Manager role.
    Leaves all other roles to default template inheritance so no other team members' layouts are affected.
    """
    user = getattr(request, "user", None)
    if user and user.is_authenticated:
        role_code = getattr(user, "role_code", None) or (
            user.role.code if hasattr(user, "role") and user.role else None
        )
        if role_code == Role.LEGAL_MGR or role_code == "LEGAL_MGR":
            return {"base_layout": "layouts/legal_base.html"}
        elif role_code == Role.STORES_RECEIVER or role_code == "STORES_RECEIVER":
            return {"base_layout": "layouts/stores_base.html"}
    return {}
