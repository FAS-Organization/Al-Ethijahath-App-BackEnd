from pathlib import Path

from django.core.exceptions import PermissionDenied
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404
from django.urls import reverse

from . import models as m
from .portal_admin import scoped


FILES = {
    "pdf": (m.GeneratedDocument, {"file": "Versioned PDF"}),
    "return": (m.EmailApprovalSubmission, {"signed_pdf": "Returned signed PDF", "email_evidence": "Original email"}),
    "approval": (m.DocumentApproval, {"evidence_file": "Approval evidence"}),
    "request": (m.ServiceJob, {"request_photo": "Service request photo"}),
}


def link(kind, pk, field):
    return reverse("portal-staff-file", args=[kind, pk, field])


def download(request, kind, pk, field):
    if kind not in FILES or field not in FILES[kind][1]:
        raise Http404
    model, labels = FILES[kind]
    if not request.user.has_perm("web.view_" + model._meta.model_name):
        raise PermissionDenied()
    obj = get_object_or_404(scoped(model.objects.all(), request.user), pk=pk)
    handle = getattr(obj, field)
    if not handle:
        raise Http404
    try:
        response = FileResponse(handle.open("rb"), as_attachment=True, filename=labels[field] + Path(handle.name).suffix)
    except FileNotFoundError as exc:
        raise Http404 from exc
    response["Cache-Control"] = "private, no-store"
    return response
