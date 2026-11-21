# Customer portal backend

The customer portal uses Django under `backend/`. From that directory, install the dependencies and run the migration and tests before connecting a frontend.

```powershell
python -m pip install -r requirements.txt
python manage.py migrate
python manage.py configure_portal_roles
python manage.py test web
python manage.py runserver
```

### Regional accounts and rentals

Riyadh, Jeddah and Dammam have separate customer logins. Registration takes `fullName`, `companyName`, `email`, `phone`, `password` and either `regionId` or `region`. Staff approve the company in Django admin. Each login email identifies one user. Companies with identical trading names can register independent accounts in different regions. The migration assigns an existing company automatically if all its sites share one region. An administrator should review older accounts with no sites or conflicting regions before adding regional records.

Under **Rental items → Add**, choose the region first, then enter the item and available quantity. The catalog shows active items in the customer's region with quantity above zero. A request records the item names and quantities. Staff call the customer and record the outcome under **Rental orders → Record phone confirmation**. Staff maintain the catalog quantity separately.

### Customer API

Routes below begin with `/api/v1/`. Sign in through `auth/login/` and send the access token as a Bearer token. `auth/me/` returns the role. When refreshing, store both the new access token and the rotated refresh token. Authentication endpoints also include `auth/register/`, `auth/regions/` and `auth/logout/`.

| Customer page | API |
| --- | --- |
| Dashboard, equipment and job history | GET `customer/dashboard/`, `customer/equipment/`, `customer/services/` |
| Request service | GET `customer/service-requests/` for permitted sites and equipment, then POST there with `site_id`, repeated `equipment_ids`, `description`, optional `preferred_date` and optional multipart `photo` |
| Account | GET `customer/account/`, PATCH `customer/profile/`, POST `customer/sites/`, PATCH `customer/sites/{id}/`, PUT `customer/account/preferences/`, POST `customer/account/password/` |
| Account sharing | POST `customer/service-access/challenges/` with the recipient's email, POST `customer/service-access/challenges/{uuid}/verify/` with the four-digit code, then POST `customer/service-access/` with `challenge`, `email` and `password`. GET the same collection to list delegates. PATCH `customer/service-access/{id}/` with `is_enabled`. Credential edits require a fresh challenge bound to that delegate using `service_user_id`. |
| Named report signers | GET or POST `customer/approvers/`, PATCH `customer/approvers/{id}/` |
| Inspections and quotations | GET `customer/quotations/`. POST `customer/quotations/{id}/decision/` with `version`, `decision` and a purchase order for approval or optional reason for rejection. POST `customer/quotations/{id}/email/` with `version` to request a PDF at the registered email. |
| Maintenance | GET `customer/maintenance/`, POST `customer/appointments/{id}/reschedule/` with `date` and `reason`. POST `customer/reschedules/{id}/respond/` with `withdraw` or `accept_alternative` in `action`. The original visit stays scheduled until staff confirm exact times. |
| Invoices and statement | GET `customer/billing/`, `customer/billing/ledger/`, `customer/billing/statement/` (PDF) |
| Rentals | GET `customer/rentals/`, POST there with `items: [{id, quantity}]` using catalog codes |
| Documents and alerts | GET `customer/documents/`. Follow the returned authenticated file URLs. GET `customer/notifications/`, POST `customer/notifications/{id}/read/` with `read`, or POST `customer/notifications/read/` to mark all read. |
| EthiBot | POST `customer/chat/` with `messages` containing `role` and `text`. It reads the signed-in owner's balances, pending charges and service progress. |

Service-only users receive the `service_requester` role. Render the familiar portal shell, enable theme switching and the service form, and avoid requesting owner data for the locked sections. A delegated service request belongs to the original company and records the delegate as its requester. A password reset or access change invalidates earlier delegate sessions. The production API supports setting a replacement password. The eye control can reveal what the owner is typing. An existing password is stored as a one-way hash.

### Staff operations

After running `configure_portal_roles`, a superuser assigns staff to their existing employee region and appropriate group. The command adds baseline permissions for Manager, Admin, Technician and Sales Executive and preserves existing custom grants. A financial clerk can receive `web.view_invoice` and `web.add_creditnote` with their employee region for credit entry.

- Dispatch a technician from the machine service. The technician accepts or declines their own assignment. Mark a service update as customer-visible to show it in the portal.
- Submit the inspection or completion revision, then open **Record on-site approval** on the assigned technician's tablet. The account holder or an active named signer can sign there. The staff screen also accepts a captured image. Sales can verify a returned signed PDF against its exact source version and email evidence.
- Issue a quotation or invoice from its draft and lines. The operation captures the company billing details, obtains a regional number and generates a private, versioned PDF. For a quotation accepted by email, Sales records the registered sender, copied purchasing contact or approver, PO information and original email.
- Open **Invoices → Issue credit note** to enter the unique note number, amount and reason. The remaining balance equals the issued total less posted payments and issued credit notes. An invoice for SAR 100 with SAR 24 paid and SAR 30 credited has SAR 46 outstanding. The audit record keeps the staff member and the statement shows the credit separately. Staff can correct an erroneous payment by voiding the entry with a reason and posting a replacement.
- Use the regional **Reminder policies** to schedule visit, quotation and overdue reminders. Schedule `python manage.py process_portal` every five minutes to deliver queued mail, run reminders and generate completed months of AMC summaries. Contract summaries are stable once published.

### Staging and frontend wiring

Use PostgreSQL in staging to exercise invoice row locking. Configure `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_HOST` and `POSTGRES_PORT` (the corresponding `DB_*` variables also work). Set `DEBUG=False`, a private `SECRET_KEY`, `ALLOWED_HOSTS`, `CORS_ALLOWED_ORIGINS` and `CSRF_TRUSTED_ORIGINS`. Run `python manage.py collectstatic --noinput` for admin assets.

Configure SMTP through `EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend`, `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `DEFAULT_FROM_EMAIL` and the TLS or SSL setting for your provider. OTP and registration mail use this connection. The worker processes queued notices and requested quotation PDFs. The launch sends in-app notifications and email. SMS and WhatsApp choices are saved for future delivery integrations.

Use a private R2 bucket dedicated to portal documents. Set `R2_PRIVATE_BUCKET`, the account-level `R2_ENDPOINT_URL`, `R2_ACCESS_KEY_ID` and `R2_SECRET_ACCESS_KEY`. Keep public access disabled for this bucket. For local testing, the default private files live in Django's media directory and are served through the authenticated file views. PDFs can use a custom font through `PORTAL_PDF_FONT`. Set `GEMINI_API_KEY` on the backend and optionally `GEMINI_MODEL`.

The React `demo` branch currently uses preview data, locally generated OTPs, an in-memory password map and browser-side EthiBot. Replace those with the API callbacks when wiring the frontend. Load private downloads with the Bearer token and create a browser object URL from the response. Bind balance cards to the returned outstanding balance so credit notes reduce what the customer sees. Keep the customer-facing inspection label “Awaiting approval” in the document archive as well as the quotations page.

The integration tests run with a temporary file store and in-memory mailbox. Before release, exercise a real recipient OTP, a signed document round trip in private R2, the scheduled worker and a live EthiBot request in staging.
