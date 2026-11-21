from collections import defaultdict
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.db.models import Q
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from web.models import (
    AMCEquipmentCoverage,
    InspectionReport,
    Invoice,
    JobEquipment,
    PendingServiceCharge,
    Quotation,
    ServiceUpdate,
)
from .documents import file_url, pdfs
from .ledger import invoice_ledger
from .permissions import IsCustomer


def fields(obj, *names):
    return {name: getattr(obj, name) for name in names}


def site(s):
    return {
        **fields(s, "id", "name", "address"),
        "region": fields(s.region, "id", "name", "currency"),
    }


def equipment_item(e):
    return fields(
        e, "id", "equipment_type", "model_name", "brand", "serial_number", "customer_asset_number"
    )


def visit_row(v):
    m = v.job_equipment
    open_req = next(
        (r for r in v.reschedule_requests.all() if r.status in ("pending", "alternative")), None
    )
    return {
        **fields(
            v, "id", "purpose", "status", "scheduled_start", "scheduled_end", "schedule_version"
        ),
        "site_id": m.job.site_id,
        "equipment_id": m.equipment_id,
        "reference": m.reference,
        "amc_due_date": v.original_due_date,
        "is_amc": bool(m.amc_coverage_id),
        "technician": v.technician.user.get_full_name() if v.technician_id else None,
        "can_reschedule": v.status in ("planned", "confirmed")
        and v.scheduled_start > timezone.now()
        and not open_req,
        "reschedule": (
            fields(open_req, "id", "status", "alternative_date", "proposed_date")
            if open_req
            else None
        ),
    }


def jobs_for(c):
    jobs = (
        c.jobs.select_related("site__region")
        .prefetch_related(
            "machines__equipment",
            "machines__quotations",
            "machines__assigned_technician__user",
            "machines__appointments__technician__user",
            "machines__appointments__reschedule_requests",
        )
        .order_by("-pk")
    )
    updates = defaultdict(list)
    for u in ServiceUpdate.objects.filter(job_equipment__job__company=c, visibility="customer"):
        updates[u.job_equipment_id].append(fields(u, "id", "status", "message", "event_at"))
    docs = defaultdict(list)
    for p in pdfs(c).select_related(
        "inspection_report", "completion_card", "quotation", "template"
    ):
        src = p.inspection_report or p.completion_card or p.quotation
        if src:
            docs[src.job_equipment_id].append(
                {
                    "id": p.pk,
                    "document_type": p.template.document_type,
                    "document_number": p.document_number,
                    "status": src.status,
                    "file_url": file_url("pdf", p.pk),
                }
            )
    result = []
    for j in jobs:
        machines = []
        for m in j.machines.all():
            quotes = list(m.quotations.all())
            public_summary = next(
                (q.customer_summary for q in quotes if q.status == "approved"), ""
            )
            visits = sorted(
                (
                    v
                    for v in m.appointments.all()
                    if v.scheduled_start > timezone.now() and v.status in ("planned", "confirmed")
                ),
                key=lambda v: v.scheduled_start,
            )
            e = equipment_item(m.equipment)
            for k in e.keys() - {"id"}:
                if k in m.equipment_snapshot:
                    e[k] = m.equipment_snapshot[k]
            machines.append(
                {
                    **fields(
                        m,
                        "id",
                        "status",
                        "suffix",
                        "reference",
                        "reported_issue",
                        "started_at",
                        "completed_at",
                        "estimated_completion",
                    ),
                    "equipment": e,
                    "planned_work": public_summary,
                    "next_appointment": visit_row(visits[0]) if visits else None,
                    "technician_name": (
                        m.assigned_technician.user.get_full_name()
                        if m.assigned_technician_id
                        else None
                    ),
                    "pending_actions": [
                        {
                            "id": q.pk,
                            "type": "quotation",
                            "label": "Review quotation",
                            "detail": q.number,
                        }
                        for q in quotes
                        if q.status == "sent" and not q.is_expired
                    ],
                    "updates": updates[m.pk],
                    "documents": docs[m.pk],
                }
            )
        result.append(
            {
                **fields(
                    j,
                    "id",
                    "job_type",
                    "status",
                    "description",
                    "requested_date",
                    "site_address_snapshot",
                    "created_at",
                ),
                "site": site(j.site),
                "machines": machines,
            }
        )
    return result


def effective_coverage(c):
    today = timezone.localdate(
        timezone=ZoneInfo(c.region.timezone if c.region_id else "Asia/Riyadh")
    )
    return AMCEquipmentCoverage.objects.filter(
        contract__company=c,
        equipment__company=c,
        contract__status="active",
        is_active=True,
        start_date__lte=today,
        end_date__gte=today,
        contract__start_date__lte=today,
        contract__end_date__gte=today,
    ).select_related("contract")


def equipment_for(c):
    coverages = defaultdict(list)
    for cv in effective_coverage(c):
        coverages[cv.equipment_id].append(
            {
                **fields(cv, "id", "start_date", "end_date", "next_service_due_date"),
                "contract": fields(cv.contract, "contract_number"),
            }
        )
    history = defaultdict(list)
    for m in (
        JobEquipment.objects.filter(job__company=c, equipment__company=c)
        .prefetch_related("quotations")
        .order_by("-created_at")
    ):
        history[m.equipment_id].append(
            {
                **fields(
                    m,
                    "id",
                    "status",
                    "reported_issue",
                    "started_at",
                    "completed_at",
                    "estimated_completion",
                ),
                "job": {"id": m.job_id},
                "planned_work": next(
                    (q.customer_summary for q in m.quotations.all() if q.status == "approved"), ""
                ),
            }
        )
    return [
        {
            **equipment_item(e),
            **fields(e, "fuel_type", "capacity", "manufacture_year", "is_retired"),
            "current_site": site(e.current_site) if e.current_site_id else None,
            "current_amc_coverages": coverages[e.pk],
            "service_records": history[e.pk],
        }
        for e in c.equipment.select_related("current_site__region")
    ]


def quotes_for(c):
    documents = {
        p.quotation_id: p.pk for p in pdfs(c).exclude(quotation=None).order_by("published_at", "pk")
    }
    result = []
    for q in (
        Quotation.objects.filter(job_equipment__job__company=c)
        .exclude(status="draft")
        .select_related(
            "job_equipment__equipment", "job_equipment__job__site", "inspection_report", "region"
        )
        .order_by("-version")
    ):
        m = q.job_equipment
        result.append(
            {
                **fields(
                    q,
                    "id",
                    "number",
                    "version",
                    "currency",
                    "customer_summary",
                    "estimated_completion_text",
                    "terms",
                    "sent_at",
                    "expires_at",
                    "decided_at",
                    "rejection_reason",
                    "inspection_report_id",
                ),
                "previous_version": q.previous_version_id,
                "status": "expired" if q.is_expired else q.status,
                "total": str(q.total),
                "company_name": c.name,
                "region_name": q.region.name,
                "inspection_reference": f"INS-{m.reference} · Version {q.inspection_report.version}",
                "job_equipment": {
                    "id": m.pk,
                    "reference": m.reference,
                    "equipment_name": m.equipment.equipment_type,
                    "model_name": m.equipment.model_name,
                    "asset_number": m.equipment.customer_asset_number,
                    "site_name": m.job.site.name,
                },
                "pdf_url": file_url("pdf", documents[q.pk]) if q.pk in documents else None,
            }
        )
    return result


def inspections_for(c):
    docs = {
        p.inspection_report_id: p
        for p in pdfs(c).exclude(inspection_report=None).order_by("published_at", "pk")
    }
    data = []
    for r in (
        InspectionReport.objects.filter(job_equipment__job__company=c, purpose="customer")
        .exclude(status="draft")
        .select_related("job_equipment__job__site", "job_equipment__equipment")
        .prefetch_related("findings")
    ):
        m = r.job_equipment
        doc = docs.get(r.pk)
        data.append(
            {
                **fields(r, "id", "version", "status", "remarks", "inspected_at"),
                "serviceReference": m.reference,
                "site": m.job.site.name,
                "equipment": str(m.equipment),
                "number": doc.document_number if doc else "INS-" + m.reference,
                "pdf_url": file_url("pdf", doc.pk) if doc else None,
                "findings": [
                    fields(x, "component", "condition", "proposed_action", "description")
                    for x in r.findings.all()
                ],
            }
        )
    return data


def invoices_for(c):
    docs = {
        p.invoice_id: p.pk for p in pdfs(c).exclude(invoice=None).order_by("published_at", "pk")
    }
    rows = []
    for i in (
        c.invoices.filter(job__company=c)
        .exclude(status="draft")
        .select_related("region", "job__site__region")
        .prefetch_related("lines", "job__machines__equipment", "payments", "credit_notes")
    ):
        m = {x.pk: x for x in i.job.machines.all()}
        lines = list(i.lines.all())
        billed = {x.job_equipment_id for x in lines if x.job_equipment_id}

        def labels(keys):
            return [
                {"reference": m[k].reference, "label": str(m[k].equipment)}
                for k in sorted(keys)
                if k in m
            ]

        rows.append(
            {
                **invoice_ledger(i),
                **fields(i, "void_reason"),
                "description": i.job.description,
                **{k: str(getattr(i, k)) for k in ("subtotal", "discount", "tax_amount")},
                "region": {
                    **fields(i.region, "id", "name", "currency", "bank_details"),
                    "payment_qr_url": (
                        file_url("payment-qr", i.region_id) if i.region.payment_qr else None
                    ),
                },
                "job": {**fields(i.job, "id", "job_type"), "site": site(i.job.site)},
                "is_partial_invoice": bool(billed and m.keys() - billed),
                "covers": labels(billed),
                "remaining_machines": labels(m.keys() - billed) if billed else [],
                "bill_to": {
                    k: i.billing_snapshot.get(k, "") for k in ("name", "address", "tax_number")
                },
                "lines": [
                    {
                        **fields(x, "id", "category", "description"),
                        "reference": (
                            m[x.job_equipment_id].reference if x.job_equipment_id in m else ""
                        ),
                        **{n: str(getattr(x, n)) for n in ("quantity", "unit_price", "line_total")},
                    }
                    for x in lines
                ],
                "pdf_available": i.pk in docs,
                "pdf_url": file_url("pdf", docs[i.pk]) if i.pk in docs else None,
            }
        )
    return rows


def charges_for(c):
    rows = (
        PendingServiceCharge.objects.filter(
            job_equipment__job__company=c, published_at__lte=timezone.now(), settled_offline_at=None
        )
        .filter(Q(invoice_line=None) | Q(invoice_line__invoice__status="void"))
        .select_related(
            "job_equipment__equipment", "job_equipment__job__site", "job_equipment__job__region"
        )
    )
    return [
        {
            "id": p.pk,
            "description": p.description,
            "currency": p.job_equipment.job.region.currency,
            "balanceMinor": int(p.amount * 100),
            "jobReference": p.job_equipment.reference,
            "equipment": str(p.job_equipment.equipment),
            "site": p.job_equipment.job.site.name,
        }
        for p in rows
    ]


@api_view(["GET"])
@permission_classes([IsCustomer])
def collection(request, section):
    c = request.user.customer_company
    if section == "services":
        data = {"jobs": jobs_for(c)}
    elif section == "equipment":
        data = {"equipment": equipment_for(c)}
    elif section == "billing":
        data = {"invoices": invoices_for(c), "pendingServiceCharges": charges_for(c)}
    elif section == "quotations":
        data = {"quotations": quotes_for(c), "inspections": inspections_for(c)}
    else:
        from django.http import Http404

        raise Http404
    return Response({"company_name": c.name, "is_demo": False, **data})
