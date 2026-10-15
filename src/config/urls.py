from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path, re_path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

from apps.accounts.api_views import SessionLoginView, SessionLogoutView
<<<<<<< Updated upstream
from apps.accounts.views import password_change_view, portal_login_view, portal_logout_view
=======
from apps.accounts.views import change_password_view, portal_login_view, portal_logout_view
>>>>>>> Stashed changes
from apps.core.views import custom_404_view

from .healthchecks import health, readyz

urlpatterns = [
    path("health", health, name="health"),
    path("readyz", readyz, name="readyz"),
    re_path(r"^.*prompts\.json$", lambda request: JsonResponse({})),
    path("admin/logout/", portal_logout_view, name="admin_logout_override"),
    path("admin/", admin.site.urls),
    path("login/", portal_login_view, name="login"),
    path("logout/", portal_logout_view, name="logout"),
<<<<<<< Updated upstream
    path("password-change/", password_change_view, name="password_change"),
=======
    path("accounts/change-password/", change_password_view, name="change_password"),
>>>>>>> Stashed changes
    # Superadmin, Manager & Vendor Portals
    path("superadmin/", include("apps.core.superadmin_urls")),
    path("manager/", include("apps.core.manager_urls")),
    path("vendor/", include("apps.vendors.vendor_dashboard_urls")),
    # Core & Auth & Page Views
    path("", include("apps.core.urls")),
    path("", include("apps.accounts.urls")),
    path("", include("apps.requisitions.urls")),
    path("", include("apps.approvals.urls")),
    path("", include("apps.vendors.urls")),
    path("", include("apps.sourcing.urls")),
    path("", include("apps.orders.urls")),
    path("", include("apps.receipts.urls")),
    path("invoices/", include("apps.invoices.urls")),
    path("", include("apps.contracts.urls")),
    path("", include("apps.scorecards.urls")),
    path("", include("apps.notifications.urls")),
    path("reports/", include("apps.reports.urls")),
    path("", include("apps.audit.urls")),
    path("budgets/", include("apps.budgets.urls")),
    # OpenAPI Schema & Swagger Docs
    path("api/schema", SpectacularAPIView.as_view()),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs", SpectacularSwaggerView.as_view(url_name="schema")),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
    # Session Authentication Endpoints
    path("api/v1/auth/login/", SessionLoginView.as_view(), name="session_login"),
    path("api/v1/auth/logout/", SessionLogoutView.as_view(), name="session_logout"),
    # Domain App API Endpoints (/api/v1/...)
    path("api/v1/accounts/", include("apps.accounts.api_urls")),
    path("api/v1/organization/", include("apps.organization.api_urls")),
    path("api/v1/approvals/", include("apps.approvals.api_urls")),
    path("api/v1/vendors/", include("apps.vendors.api_urls")),
    path("api/v1/requisitions/", include("apps.requisitions.api_urls")),
    path("api/v1/sourcing-events/", include("apps.sourcing.api_urls")),
    path("api/v1/purchase-orders/", include("apps.orders.api_urls")),
    path("api/v1/receipts/", include("apps.receipts.api_urls")),
    path("api/v1/invoices/", include("apps.invoices.api_urls")),
    path("api/v1/contracts/", include("apps.contracts.api_urls")),
    path("api/v1/budgets/", include("apps.budgets.api_urls")),
    path("api/v1/scorecards/", include("apps.scorecards.api_urls")),
    path("api/v1/notifications/", include("apps.notifications.api_urls")),
    path("api/v1/reports/", include("apps.reports.api_urls")),
    path("api/v1/audit/", include("apps.audit.api_urls")),
    path("api/v1/manager/", include("apps.core.manager_api_urls")),
    re_path(r"^api/.*$", custom_404_view),
]


if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

handler404 = "apps.core.views.custom_404_view"
handler500 = "apps.core.views.custom_500_view"
handler403 = "apps.core.views.custom_403_view"
