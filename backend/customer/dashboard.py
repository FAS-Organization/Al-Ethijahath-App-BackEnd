from collections import Counter
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from web.models import ServiceAppointment
from .documents import archive
from .notices import notices_for
from .permissions import IsCustomer
from .records import charges_for, effective_coverage, invoices_for, jobs_for, quotes_for


@api_view(["GET"])
@permission_classes([IsCustomer])
def dashboard(request):
    c=request.user.customer_company
    tz=ZoneInfo(c.region.timezone if c.region_id else "Asia/Riyadh")
    today=timezone.localdate(timezone=tz)
    covered=set(effective_coverage(c).values_list("equipment_id",flat=True))
    active=[]
    counts=Counter()
    last={}
    for j in jobs_for(c):
        for m in j["machines"]:
            if m["completed_at"]:
                day=timezone.localdate(m["completed_at"],timezone=tz)
                counts[day.strftime("%Y-%m")]+=1
                key=m["equipment"]["id"]
                last[key]=max(day,last.get(key,day))
            if j["status"] not in ("completed","closed","cancelled") and m["status"] not in ("completed","cancelled"):
                active.append({"id":m["reference"],"equipment":m["equipment"]["equipment_type"],"site":j["site"]["name"],
                    "status":m["status"],"damage":m["reported_issue"],"work":m["planned_work"],"updatedAt":m["updates"][0]["event_at"] if m["updates"] else j["created_at"]})
        if not j["machines"] and j["status"] not in ("closed","completed","cancelled"):
            active.append({"id":str(j["id"]),"equipment":"","site":j["site"]["name"],"status":j["status"],"damage":j["description"],"work":"","updatedAt":j["created_at"]})
    history=[]
    for back in range(5,-1,-1):
        year,idx=divmod(today.year*12+today.month-1-back,12)
        month=today.replace(year=year,month=idx+1,day=1)
        history.append({"month":month.strftime("%b"),"completed":counts[month.strftime("%Y-%m")]})
    def minor(x):
        return int(Decimal(x)*100)
    upcoming=ServiceAppointment.objects.filter(job_equipment__job__company=c,status__in=["planned","confirmed"],scheduled_start__gt=timezone.now())
    return Response({"companyName":c.name,"equipment":[{"id":e.pk,"name":str(e),"model":e.model_name,"site":e.current_site.name if e.current_site_id else "",
            "coverage":"AMC" if e.pk in covered else "Non-AMC","status":"Retired" if e.is_retired else "In service","lastService":last.get(e.pk)}
            for e in c.equipment.select_related("current_site")],"activeJobs":active,"serviceActivity":history,
        "appointments":[{"id":v.pk,"equipment":str(v.job_equipment.equipment),"site":v.job_equipment.job.site.name,"startsAt":v.scheduled_start,
            "status":v.get_status_display(),"reschedulePending":v.reschedule_requests.filter(status__in=["pending","alternative"]).exists()}
            for v in upcoming.select_related("job_equipment__job__site","job_equipment__equipment").order_by("scheduled_start")],
        "quotations":[{"id":q["number"],"job":q["job_equipment"]["reference"],"title":q["customer_summary"],"currency":q["currency"],
            "totalMinor":minor(q["total"])} for q in quotes_for(c) if q["status"]=="sent"],
        "pendingServiceCharges":charges_for(c),"invoices":[{"id":i["number"],"description":i["description"],"currency":i["currency"],
            "totalMinor":minor(i["total"]),"paidMinor":minor(i["amount_paid"]),"creditMinor":minor(i["amount_credited"]),"balanceMinor":minor(i["balance_due"]),
            "dueDate":i["due_date"],"overdue":i["is_overdue"]} for i in invoices_for(c) if i["status"]=="issued"],
        "documents":[{"id":d["id"],"title":d["title"],"job":d.get("reference"),"type":d["type"],"issuedOn":d["date"],"url":d["file_url"]} for d in archive(c)],
        "notifications":[{"id":n["id"],"title":n["title"],"message":n["message"],"read":bool(n["read_at"]),
                          "tone":"warning" if n["requires_action"] else "normal"} for n in notices_for(c)]})
