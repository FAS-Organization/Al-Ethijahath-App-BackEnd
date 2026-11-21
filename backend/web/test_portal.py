import re
import tempfile
from datetime import timedelta
from decimal import Decimal
from io import BytesIO
from uuid import uuid4
from unittest.mock import patch

from django.core.management import call_command
from django.urls import reverse

from django.core import mail
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.utils import timezone
from PIL import Image
from rest_framework.test import APITestCase

from web import models as m

C = "/api/v1/customer/"
S = "/api/v1/staff/"
A = "/api/v1/auth/"


def image():
    b = BytesIO()
    Image.new("RGB",(4,4),"white").save(b,"PNG")
    return SimpleUploadedFile("signature.png",b.getvalue(),"image/png")


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"],
                   EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class PortalTests(APITestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        settings = override_settings(MEDIA_ROOT=tmp.name)
        settings.enable()
        self.addCleanup(settings.disable)
        self.r = m.Region.objects.get(name="Riyadh")
        self.j = m.Region.objects.get(name="Jeddah")
        self.owner,self.co,self.site = self.company("owner",self.r)
        self.other,self.co2,self.site2 = self.company("other",self.j)
        self.staff = m.User.objects.create_superuser("staff@example.com","Staff-test-582!")
        self.e = m.Equipment.objects.create(company=self.co,current_site=self.site,equipment_type="Crane")
        self.client.force_authenticate(self.owner)

    def company(self, name, r):
        u = m.User.objects.create_user(name+"@example.com","Test-462!word",is_active=True,account_status="active")
        c = m.CustomerCompany.objects.create(account_holder=u,region=r,name=name,contact_name=name,contact_email=u.email)
        return u,c,m.CustomerSite.objects.create(company=c,region=r,name=name,address="Warehouse")

    def post(self, path, data, status=200, fmt="json"):
        res=self.client.post(path,data,format=fmt)
        self.assertEqual(res.status_code,status,getattr(res,"data",""))
        return res.data

    def job(self):
        return m.ServiceJob.objects.create(company=self.co,site=self.site,region=self.r,job_type="service",description="Repair forklift")

    def invoice(self):
        job=self.job()
        now=timezone.now()
        return m.Invoice.objects.create(company=self.co,region=self.r,job=job,status="issued",issued_at=now,number="INV-1",
            currency="SAR",due_date=now.date(),issue_date=now.date(),subtotal=Decimal("100.00"),total=Decimal("100.00"))

    def test_credits_separate_from_payments_and_preserve_invoice(self):
        invoice=self.invoice()
        m.Payment.objects.create(invoice=invoice,amount=Decimal("20"),method="cash",paid_at=timezone.now())
        path=S+f"invoices/{invoice.pk}/credit-notes/"
        data={"amount":"30.00","reason":"Corrected labour charge","number":"CN-1"}
        self.post(path,data,403)
        self.client.force_authenticate(self.staff)
        note=self.post(path,data,201)
        self.assertEqual(self.post(path,data)["id"],note["id"])
        self.post(path,{**data,"amount":"51","number":"CN-2"},400)
        self.client.force_authenticate(self.owner)
        row=self.client.get(C+"billing/ledger/").data["invoices"][0]
        self.assertEqual((row["total"],row["amount_paid"],row["amount_credited"],row["balance_due"]),
                         ("100.00","20.00","30.00","50.00"))
        with self.assertRaises(ValidationError):
            m.Payment.objects.create(invoice=invoice,amount=Decimal("51"),method="cash",paid_at=timezone.now())
        self.client.force_authenticate(self.staff)
        self.post(path,{**data,"amount":"50","number":"CN-2"},201)
        self.post(path,{**data,"amount":"1","number":"CN-3"},400)
        self.assertEqual(invoice.payment_status,"paid")
        self.assertEqual(invoice.total,Decimal("100"))
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get(C+"billing/ledger/").data["invoices"],[])

    def test_regional_catalog_and_manual_rental(self):
        item=m.RentalItem.objects.create(region=self.r,name="Drill",code="drill",available=2)
        m.RentalItem.objects.create(region=self.r,name="Empty",code="empty",available=0)
        m.RentalItem.objects.create(region=self.j,name="Lift",code="lift",available=4)
        self.assertEqual([x["id"] for x in self.client.get(C+"rentals/").data["items"]],["drill"])
        for code in ["empty","lift"]:
            self.post(C+"rentals/",{"items":[{"id":code,"quantity":1}]},400)
        o=self.post(C+"rentals/",{"items":[{"id":"drill","quantity":2}]},201)
        self.assertEqual(o["status"],"processing")
        self.client.force_authenticate(self.staff)
        self.post(S+f"rentals/{o['order_id']}/review/",{"status":"confirmed"})
        item.refresh_from_db()
        self.assertEqual(item.available,2)
        self.client.force_authenticate(self.other)
        self.assertFalse(self.client.get(C+"rentals/").data["orders"])

    def test_otp_delegation_and_revoke_old_tokens(self):
        r=self.post(C+"service-access/challenges/",{"email":"helper@example.com"},201)
        self.assertFalse(m.User.objects.filter(email="helper@example.com").exists())
        code=re.search(r"code (\d{4})",mail.outbox[-1].body)[1]
        path=C+f"service-access/challenges/{r['challenge']}/verify/"
        self.post(path,{"code":f"{(int(code)+1)%10000:04d}"},400)
        self.post(path,{"code":code})
        v={"challenge":str(r["challenge"]),"email":"helper@example.com","password":"secret-617x!new"}
        g=self.post(C+"service-access/",v,201)
        self.post(C+"service-access/",v,404)
        self.assertNotIn("password",g)
        self.client.force_authenticate(None)
        tokens=self.post(A+"login/",{"email":v["email"],"password":v["password"]})
        self.client.credentials(HTTP_AUTHORIZATION="Bearer "+tokens["access"])
        self.assertEqual(self.client.get(C+"service-requests/").status_code,200)
        for path in ["billing/ledger/","profile/","account/","rentals/","service-access/"]:
            self.assertEqual(self.client.get(C+path).status_code,403,path)
        self.post(C+"service-requests/",{"site_id":self.site2.pk,"description":"Repair my forklift"},404)
        job=self.post(C+"service-requests/",{"site_id":self.site.pk,"equipment_ids":[self.e.pk],
            "description":"Repair my forklift","photo":image()},201,"multipart")
        stored=m.ServiceJob.objects.get(pk=job["id"])
        self.assertEqual((stored.company,stored.requested_by.email),(self.co,v["email"]))
        self.assertTrue(stored.request_photo.storage.exists(stored.request_photo.name))
        self.client.force_authenticate(self.owner)
        for enabled in [False,True]:
            self.assertEqual(self.client.patch(C+f"service-access/{g['id']}/",{"is_enabled":enabled},format="json").status_code,200)
        self.client.force_authenticate(None)
        self.client.credentials(HTTP_AUTHORIZATION="Bearer "+tokens["access"])
        self.assertEqual(self.client.get(C+"service-requests/").status_code,401)
        self.post(A+"refresh/",{"refresh":tokens["refresh"]},401)

    def test_region_and_preferences(self):
        self.post(C+"sites/",{"name":"Main","kind":"warehouse","address":"Same region"},201)
        self.post(C+"sites/",{"name":"Remote","region":self.j.pk,"address":"Other region"},400)
        flags={"channels":{"email":False,"whatsapp":False,"sms":True},"topics":{"quotations":True,"visits":False,"payments":True}}
        self.assertEqual(self.client.put(C+"account/preferences/",flags,format="json").status_code,200)
        self.assertEqual(self.client.get(C+"account/").data["account"]["preferences"],flags)

    def machine_and_technician(self):
        tech = m.EmployeeProfile.objects.create(user=self.staff, region=self.r, employee_id="T-001", job_title="Technician")
        machine = m.JobEquipment.objects.create(job=self.job(), equipment=self.e, suffix="A", assigned_technician=tech)
        return machine, tech

    def report(self):
        machine, tech = self.machine_and_technician()
        return m.InspectionReport.objects.create(job_equipment=machine, technician=tech, inspected_at=timezone.now(),
            remarks="Repair required", status="pending_approval", submitted_at=timezone.now())

    def signed(self, report):
        self.client.force_authenticate(self.staff)
        return self.post(S+f"reports/inspection/{report.pk}/tablet-approval/", {
            "signer_name":self.co.contact_name, "customer_signature":image(), "technician_signature":image()
        }, fmt="multipart")

    def test_tablet_approval_and_private_published_report(self):
        report=self.report()
        url=S+f"reports/inspection/{report.pk}/tablet-approval/"
        self.post(url,{},403)
        self.client.force_authenticate(self.staff)
        self.post(url,{"signer_name":"Unregistered signer","customer_signature":image(),"technician_signature":image()},400,"multipart")
        self.assertEqual(report.approvals.count(),0)
        self.assertEqual(self.signed(report)["status"],"approved")
        self.assertEqual(set(report.approvals.values_list("signer_role",flat=True)),{"customer","technician"})
        self.post(url,{},400)
        document=self.post(S+f"documents/inspection/{report.pk}/publish/",{})
        self.assertEqual(self.post(S+f"documents/inspection/{report.pk}/publish/",{})["id"],document["id"])
        url=C+f"files/pdf/{document['id']}/"
        self.client.force_authenticate(self.owner)
        response=self.client.get(url)
        self.assertEqual(response.status_code,200)
        self.assertTrue(b"".join(response.streaming_content).startswith(b"%PDF-"))
        self.assertEqual(self.client.get(C+"documents/").data["documents"][0]["source_status"],"approved")
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get(url).status_code,404)

    def quote(self, report):
        return m.Quotation.objects.create(job_equipment=report.job_equipment, inspection_report=report, region=self.r,
            currency="SAR", customer_summary="Replace seals", estimated_completion_text="Two working days",
            status="sent", sent_at=timezone.now(), expires_at=timezone.now()+timedelta(days=4), number="Q-5")

    def test_quote_decision_and_verified_email_version(self):
        report=self.report()
        self.signed(report)
        quote=self.quote(report)
        doc=self.post(S+f"documents/quotation/{quote.pk}/publish/",{})
        self.client.force_authenticate(self.owner)
        self.post(C+f"quotations/{quote.pk}/decision/",{"version":1,"decision":"approved"},400)
        queued=self.post(C+f"quotations/{quote.pk}/email/",{"version":1},202)
        call_command("process_portal")
        email_req=m.QuoteEmailRequest.objects.get(pk=queued["id"])
        self.assertIsNotNone(email_req.sent_at)
        emails=[e for e in mail.outbox if e.attachments]
        self.assertEqual(emails[0].to,[self.owner.email])
        self.assertTrue(emails[0].attachments[0].content.startswith(b"%PDF-"))
        self.client.force_authenticate(self.staff)
        url=S+f"quotations/{quote.pk}/verify-reply/"
        data={"version":1,"email_request":email_req.pk,"sender":self.other.email,"cc":"purchasing@example.com","role":"Owner",
              "signer_name":self.co.contact_name,"purchase_order_note":"Confirmed company does not issue a PO"}
        def proof():
            return SimpleUploadedFile("original.eml",b"Customer reply","message/rfc822")
        self.post(url,{**data,"email_evidence":proof()},400,"multipart")
        self.post(url,{**data,"sender":self.owner.email,"email_evidence":proof()},200,"multipart")
        event=quote.approvals.get()
        self.assertEqual((event.reviewed_document_id,event.document_sha256,event.created_by_id),(doc["id"],doc["sha256"],self.staff.pk))
        self.client.force_authenticate(self.owner)
        self.post(C+f"quotations/{quote.pk}/decision/",{"version":1,"decision":"rejected"},400)

    def test_signed_email_return_needs_technician_and_verified_sender(self):
        report=self.report()
        self.client.force_authenticate(self.staff)
        self.post(S+f"reports/inspection/{report.pk}/technician-signature/",{"technician_signature":image()},201,"multipart")
        doc=self.post(S+f"documents/inspection/{report.pk}/publish/",{})
        pdf=m.GeneratedDocument.objects.get(pk=doc["id"])
        with pdf.file.open("rb") as fp:
            signed=fp.read()
        row=m.EmailApprovalSubmission.objects.create(source_document=pdf,signing_user=self.owner,signer_name=self.co.contact_name,
            signer_email=self.owner.email,sender_email=self.other.email,received_at=timezone.now(),
            signed_pdf=SimpleUploadedFile("signed.pdf",signed,"application/pdf"),
            email_evidence=SimpleUploadedFile("reply.eml",b"Email evidence"))
        path=S+f"reports/email-returns/{row.pk}/verify/"
        self.post(path,{},400)
        row.sender_email=self.owner.email
        row.save()
        self.post(path,{})
        result=self.post(S+f"documents/inspection/{report.pk}/publish/",{})
        self.assertEqual(result["sha256"],doc["sha256"])
        self.assertEqual(report.approvals.filter(email_submission=row).count(),1)

    def test_reschedule_keeps_original_until_staff_confirm(self):
        machine,tech=self.machine_and_technician()
        start=(timezone.now()+timedelta(days=4)).replace(hour=10,minute=0,second=0,microsecond=0)
        visit=m.ServiceAppointment.objects.create(job_equipment=machine,technician=tech,purpose="maintenance",
            scheduled_start=start,scheduled_end=start+timedelta(hours=1))
        r=self.post(C+f"appointments/{visit.pk}/reschedule/",{"date":str((start+timedelta(days=1)).date()),"reason":"Site closed"},201)
        action=S+f"reschedules/{r['id']}/review/"
        self.client.force_authenticate(self.staff)
        alt=start+timedelta(days=2)
        self.post(action,{"action":"alternative","date":str(alt.date())})
        times={"action":"confirm","start":alt.isoformat(),"end":(alt+timedelta(hours=1)).isoformat()}
        self.post(action,times,400)
        self.client.force_authenticate(self.other)
        self.post(C+f"reschedules/{r['id']}/respond/",{"action":"accept_alternative"},404)
        self.client.force_authenticate(self.owner)
        self.post(C+f"reschedules/{r['id']}/respond/",{"action":"accept_alternative"})
        visit.refresh_from_db()
        self.assertEqual(visit.scheduled_start,start)
        self.client.force_authenticate(self.staff)
        self.post(action,times)
        visit.refresh_from_db()
        self.assertEqual((visit.scheduled_start,visit.schedule_version),(alt,2))

    def test_ledger_statement_dashboard_and_chat_agree(self):
        from customer.chat import context
        invoice=self.invoice()
        self.client.force_authenticate(self.staff)
        self.post(S+f"invoices/{invoice.pk}/credit-notes/",{"number":"CN-30","reason":"Adjustment","amount":"30"},201)
        pay={"idempotency_key":str(uuid4()),"amount":"24.00","method":"cash","paid_at":timezone.now().isoformat()}
        payment=self.post(S+f"invoices/{invoice.pk}/payments/",pay,201)
        self.assertEqual(self.post(S+f"invoices/{invoice.pk}/payments/",pay)["id"],payment["id"])
        self.post(S+f"invoices/{invoice.pk}/payments/",{**pay,"amount":"10.00"},400)
        self.post(S+f"billing/invoice/{invoice.pk}/void/",{"reason":"Duplicated"},400)
        self.client.force_authenticate(self.owner)
        data=self.client.get(C+"dashboard/").data["invoices"][0]
        self.assertEqual((data["totalMinor"],data["paidMinor"],data["creditMinor"],data["balanceMinor"]),(10000,2400,3000,4600))
        self.assertEqual(context(self.co)["balances_by_currency"]["SAR"]["outstanding"],Decimal("46"))
        bill=self.client.get(C+"maintenance/").data["invoices"][0]
        self.assertEqual((bill["total"],bill["paid"],bill["credited"],bill["outstanding"]),("100.00","24.00","30.00","46.00"))
        with patch("customer.pdf.render_pdf",return_value=b"%PDF-test") as render:
            self.assertEqual(self.client.get(C+"billing/statement/").status_code,200)
        rows=render.call_args.args[2]
        self.assertEqual({(r[2],r[-1]) for r in rows[1:]},{("Invoice","100.00"),("Payment","-24.00"),("Credit CN-30","-30.00")})
        note=next(n for n in self.client.get(C+"notifications/").data["notifications"] if n["event_type"]=="credit_issued")
        self.post(C+f"notifications/{note['id']}/read/",{"read":True})
        self.assertEqual(context(self.co2)["issued_invoices"],[])

    def test_rental_admin_requires_region_and_billing_action_works_in_staff_session(self):
        from django.contrib.auth.models import Permission
        from django.test import Client
        regional=m.User.objects.create_user("counter@example.com","staff-only",is_staff=True,is_active=True,account_status="active")
        m.EmployeeProfile.objects.create(user=regional,region=self.r,employee_id="R-10",job_title="Rental counter")
        regional.user_permissions.add(*Permission.objects.filter(content_type__app_label="web",codename__in=["add_rentalitem","view_rentalitem","view_region"]))
        browser=Client()
        browser.force_login(regional)
        url=reverse("admin:web_rentalitem_add")
        form=browser.get(url).context["adminform"].form
        self.assertEqual(list(form.fields)[0],"region")
        self.assertEqual(list(form.fields["region"].queryset),[self.r])
        data={"region":self.j.pk,"code":"test","name":"Drill","category":"Tools","available":1,"is_active":"on"}
        self.assertEqual(browser.post(url,data).status_code,200)
        self.assertFalse(m.RentalItem.objects.exists())
        self.assertEqual(browser.post(url,{**data,"region":self.r.pk}).status_code,302)
        invoice=self.invoice()
        action=reverse("portal-workflow",args=["invoice-credit",invoice.pk])
        self.assertEqual(browser.get(action).status_code,403)
        browser.force_login(self.staff)
        self.assertEqual(browser.post(action,{"number":"CN-admin","reason":"Rate correction","amount":"9.00"}).status_code,302)
        self.assertEqual(invoice.balance_due,Decimal("91.00"))

    def test_credit_against_draft_and_void_and_original_total(self):
        invoice=self.invoice()
        self.client.force_authenticate(self.staff)
        endpoint=S+f"invoices/{invoice.pk}/credit-notes/"
        self.post(endpoint,{"number":"CN-01","amount":"25.00","reason":"Incorrect rate"},201)
        invoice.total=Decimal("75.00")
        with self.assertRaises(ValidationError):
            invoice.save()
        invoice.refresh_from_db()
        self.assertEqual(invoice.total,Decimal("100.00"))
        for status in ("draft","void"):
            other=m.Invoice.objects.create(company=self.co,job=self.job(),region=self.r,currency="SAR",
                status=status,issue_date=timezone.localdate(),due_date=timezone.localdate(),subtotal=50,total=50)
            self.post(S+f"invoices/{other.pk}/credit-notes/",{"number":"CN-02","amount":"5","reason":"Correction"},400)

    def test_dispatch_protects_assignments_and_internal_updates(self):
        from django.contrib.auth.models import Group, Permission
        group,_=Group.objects.get_or_create(name="Technician")
        technician=m.User.objects.create_user("tech@example.com","tech-test-only",is_staff=True,is_active=True,account_status="active")
        technician.groups.add(group)
        technician.user_permissions.add(*Permission.objects.filter(content_type__app_label="web",codename__in=["respond_to_assignment","add_serviceupdate"]))
        employee=m.EmployeeProfile.objects.create(user=technician,region=self.r,employee_id="TECH-1",job_title="Technician")
        machine=m.JobEquipment.objects.create(job=self.job(),equipment=self.e,suffix="A")
        self.client.force_authenticate(self.staff)
        assign=self.post(S+f"services/{machine.pk}/assign/",{"technician_id":employee.pk,"purpose":"inspection"},201)
        self.post(S+f"assignments/{assign['id']}/respond/",{"status":"accepted"},403)
        self.client.force_authenticate(technician)
        self.post(S+f"assignments/{assign['id']}/respond/",{"status":"accepted"})
        for visibility,note in [("internal","Internal supplier amount"),("customer","Inspection scheduled")]:
            self.post(S+f"services/{machine.pk}/progress/",{"status":"inspection","visibility":visibility,"message":note},201)
        self.client.force_authenticate(self.owner)
        visible=self.client.get(C+"services/").data["jobs"][0]["machines"][0]["updates"]
        self.assertEqual([row["message"] for row in visible],["Inspection scheduled"])

    def test_monthly_summaries_backfill_missed_months_only_once(self):
        from datetime import date
        today=timezone.localdate()
        this_month=today.replace(day=1)
        last=(this_month-timedelta(days=1)).replace(day=1)
        first=(last-timedelta(days=1)).replace(day=1)
        contract=m.AMCContract.objects.create(company=self.co,start_date=first,end_date=this_month+timedelta(days=20),
                  contract_number="AMC-9",status="active",service_interval_months=1)
        coverage=m.AMCEquipmentCoverage.objects.create(contract=contract,equipment=self.e,internal_reference="C-001",
                                                       start_date=first,end_date=contract.end_date)
        machine,tech=self.machine_and_technician()
        machine.amc_coverage=coverage
        machine.save()
        time=timezone.now().replace(year=first.year,month=first.month,day=7,hour=8)
        m.ServiceAppointment.objects.create(job_equipment=machine,technician=tech,purpose="maintenance",status="completed",
            scheduled_start=time,scheduled_end=time+timedelta(hours=1),actual_end=time+timedelta(hours=1))
        for _ in range(2):
            call_command("process_portal")
        self.assertEqual(contract.monthly_summaries.count(),2)
        self.assertEqual(len(contract.monthly_summaries.get(month_start=first).visits),1)
        self.assertEqual(len(contract.monthly_summaries.get(month_start=last).visits),0)
        self.assertEqual(self.co.account_holder.notifications.filter(event_type="amc_summary").count(),2)
        doc=contract.monthly_summaries.get(month_start=first)
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get(C+f"files/monthly/{doc.pk}/").status_code,404)

    def test_approval_portal_requires_po_and_current_version(self):
        report=self.report()
        self.signed(report)
        quote=self.quote(report)
        self.client.force_authenticate(self.other)
        self.post(C+f"quotations/{quote.pk}/decision/",{"version":1,"decision":"approved"},404)
        self.client.force_authenticate(self.owner)
        def po():
            return SimpleUploadedFile("purchase-order.pdf",b"%PDF-1.4 approved order","application/pdf")
        self.post(C+f"quotations/{quote.pk}/decision/",{"version":2,"decision":"approved","purchase_order":po()},400,"multipart")
        self.post(C+f"quotations/{quote.pk}/decision/",{"version":1,"decision":"approved","purchase_order":po()},200,"multipart")
        self.assertTrue(quote.approvals.get().evidence_file)
