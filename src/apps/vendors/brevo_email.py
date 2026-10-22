"""
Brevo (formerly Sendinblue) email helper for ProcureSphere 360.

HTML body for the vendor credential email. Delivery goes through Django's mail layer
(apps.vendors.services.send_mail) over the Brevo SMTP relay (smtp-relay.brevo.com).
The SMTP key (xsmtpsib-...) is used as EMAIL_HOST_PASSWORD.

Settings required in .env:
    EMAIL_HOST=smtp-relay.brevo.com
    EMAIL_PORT=587
    EMAIL_HOST_USER=<your brevo login email>
    EMAIL_HOST_PASSWORD=<your xsmtpsib-... SMTP key>
    EMAIL_USE_TLS=True
    DEFAULT_FROM_EMAIL=Name <email>
"""



def build_credentials_html(
    *, vendor_name: str, vendor_number: str, email: str, password: str
) -> str:
    """Returns a styled HTML email body for vendor credential dispatch."""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>ProcureSphere 360 - Supplier Portal Access</title>
</head>
<body style="margin:0;padding:0;background:#f1f5f9;font-family:Segoe UI,Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#f1f5f9;padding:40px 0;">
    <tr>
      <td align="center">
        <table width="600" cellpadding="0" cellspacing="0"
               style="background:#ffffff;border-radius:12px;
                      box-shadow:0 4px 24px rgba(0,0,0,0.08);overflow:hidden;">
          <tr>
            <td style="background:linear-gradient(135deg,#1e3a5f 0%,#2563eb 100%);
                        padding:36px 40px;text-align:center;">
              <h1 style="margin:0;color:#ffffff;font-size:22px;font-weight:700;letter-spacing:0.5px;">
                ProcureSphere <span style="color:#93c5fd;">360</span>
              </h1>
              <p style="margin:6px 0 0;color:#bfdbfe;font-size:13px;">Enterprise Procurement Platform</p>
            </td>
          </tr>
          <tr>
            <td style="padding:40px 40px 32px;">
              <p style="margin:0 0 16px;color:#374151;font-size:15px;">
                Dear <strong>{vendor_name}</strong>,
              </p>
              <p style="margin:0 0 24px;color:#6b7280;font-size:14px;line-height:1.6;">
                You have been registered as a supplier on <strong>ProcureSphere 360</strong>.
                Your portal access credentials are provided below.
              </p>
              <table width="100%" cellpadding="0" cellspacing="0"
                     style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;margin-bottom:28px;">
                <tr>
                  <td style="padding:24px 28px;">
                    <table width="100%" cellpadding="0" cellspacing="0">
                      <tr>
                        <td style="padding:6px 0;color:#6b7280;font-size:13px;font-weight:600;
                                    text-transform:uppercase;letter-spacing:0.5px;width:40%;">
                          Vendor Number
                        </td>
                        <td style="padding:6px 0;color:#111827;font-size:14px;font-weight:600;">
                          {vendor_number}
                        </td>
                      </tr>
                      <tr>
                        <td style="padding:6px 0;color:#6b7280;font-size:13px;font-weight:600;
                                    text-transform:uppercase;letter-spacing:0.5px;">
                          Login Email
                        </td>
                        <td style="padding:6px 0;color:#111827;font-size:14px;">{email}</td>
                      </tr>
                      <tr>
                        <td style="padding:6px 0;color:#6b7280;font-size:13px;font-weight:600;
                                    text-transform:uppercase;letter-spacing:0.5px;">
                          Temporary Password
                        </td>
                        <td style="padding:6px 0;">
                          <code style="background:#1e3a5f;color:#93c5fd;padding:4px 10px;
                                       border-radius:4px;font-size:14px;font-family:monospace;
                                       letter-spacing:1px;">{password}</code>
                        </td>
                      </tr>
                    </table>
                  </td>
                </tr>
              </table>
              <table width="100%" cellpadding="0" cellspacing="0"
                     style="background:#fffbeb;border-left:4px solid #f59e0b;border-radius:4px;margin-bottom:24px;">
                <tr>
                  <td style="padding:14px 18px;color:#92400e;font-size:13px;line-height:1.5;">
                    <strong>Important:</strong> You will be required to change your password on first login.
                    Please complete your KYC details and upload required documents after signing in.
                  </td>
                </tr>
              </table>
              <p style="margin:0;color:#9ca3af;font-size:12px;line-height:1.6;">
                If you did not expect this email, please ignore it and contact the procurement team immediately.
              </p>
            </td>
          </tr>
          <tr>
            <td style="background:#f8fafc;border-top:1px solid #e2e8f0;padding:20px 40px;text-align:center;">
              <p style="margin:0;color:#9ca3af;font-size:11px;">
                This is an automated message from ProcureSphere 360. Please do not reply to this email.
              </p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""
