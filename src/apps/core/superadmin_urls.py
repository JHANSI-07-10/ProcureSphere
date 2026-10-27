from django.urls import path

from . import superadmin_views

app_name = "superadmin"

urlpatterns = [
    path("", superadmin_views.superadmin_home_view, name="home"),
    path("users/", superadmin_views.user_list_view, name="users"),
    path("users/create/", superadmin_views.user_create_view, name="user_create"),
    path("users/<uuid:user_id>/edit/", superadmin_views.user_edit_view, name="user_edit"),
    path("users/<uuid:user_id>/resend-welcome/", superadmin_views.user_resend_welcome_email_view, name="user_resend_welcome"),
    path("organizations/", superadmin_views.org_list_view, name="organizations"),
    path("organizations/create/", superadmin_views.org_create_view, name="org_create"),
    path("organizations/<uuid:org_id>/edit/", superadmin_views.org_edit_view, name="org_edit"),
    path("departments/create/", superadmin_views.dept_create_view, name="dept_create"),
    path("departments/<uuid:dept_id>/edit/", superadmin_views.dept_edit_view, name="dept_edit"),
    path("cost-centers/create/", superadmin_views.cost_center_create_view, name="cost_center_create"),
    path("cost-centers/<uuid:cc_id>/edit/", superadmin_views.cost_center_edit_view, name="cost_center_edit"),
    path("cost-centers/options/", superadmin_views.cost_center_options_view, name="cost_center_options"),
    path("budgets/", superadmin_views.budget_list_view, name="budgets"),
    path("budgets/create/", superadmin_views.budget_create_view, name="budget_create"),
    path("budgets/<uuid:budget_id>/edit/", superadmin_views.budget_edit_view, name="budget_edit"),
    path("policies/", superadmin_views.policy_list_view, name="policies"),
    path("policies/create/", superadmin_views.policy_create_view, name="policy_create"),
    path("policies/<uuid:policy_id>/edit/", superadmin_views.policy_edit_view, name="policy_edit"),
    path("audit/", superadmin_views.audit_list_view, name="audit"),
    path("audit/export/", superadmin_views.audit_export_view, name="audit_export"),
]
