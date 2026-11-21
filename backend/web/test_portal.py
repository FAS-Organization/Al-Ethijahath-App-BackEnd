import re
import tempfile
from datetime import timedelta
from decimal import Decimal
from io import BytesIO

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
