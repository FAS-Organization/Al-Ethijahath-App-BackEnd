"""Small staff forms for operations that capture evidence or change a customer balance."""
from decimal import Decimal
from uuid import uuid4

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from customer import decisions, ledger, rentals, schedule
from . import models as m
from . import dispatch, operations, publication
from .portal_admin import scoped
from .staff_files import link


class Credit(forms.Form):
    number = forms.CharField(max_length=80, label="Credit note number")
    amount = forms.DecimalField(min_value=Decimal("0.01"), decimal_places=2)
    reason = forms.CharField(widget=forms.Textarea)


class Pay(forms.Form):
    amount = forms.DecimalField(min_value=Decimal("0.01"), decimal_places=2)
    method = forms.ChoiceField(choices=m.Payment._meta.get_field("method").choices)
    paid_at = forms.DateTimeField(initial=timezone.now)
    transaction_reference = forms.CharField(required=False)
    corrects_id = forms.IntegerField(required=False,label="Voided payment ID being replaced (optional)")
    idempotency_key = forms.UUIDField(widget=forms.HiddenInput, initial=uuid4)


class Reason(forms.Form):
    reason = forms.CharField(widget=forms.Textarea)


class IssueDocument(forms.Form):
    discount = forms.DecimalField(min_value=0, decimal_places=2, required=False, label="Discount (optional override)")
    tax_amount = forms.DecimalField(min_value=0, decimal_places=2, required=False, label="Tax (optional override)")


class IssueQuote(IssueDocument):
    expires_at = forms.DateTimeField(label="Quotation expiry (include the time zone)")


class TechnicianSignature(forms.Form):
    technician_signature = forms.ImageField(widget=forms.ClearableFileInput(attrs={"data-signature-pad":"true"}),label="Signature captured on the technician's tablet")


class Tablet(forms.Form):
    signer_name = forms.CharField(label="Registered name of the account holder or authorized signer")
    approver_id = forms.IntegerField(required=False, label="Named approver ID (leave blank for the account holder)")
    customer_signature = forms.ImageField(widget=forms.ClearableFileInput(attrs={"data-signature-pad":"true"}),label="Customer signature captured on site")
    technician_signature = forms.ImageField(widget=forms.ClearableFileInput(attrs={"data-signature-pad":"true"}),required=False, label="Technician signature (if not recorded yet)")


class QuoteReply(forms.Form):
    version = forms.IntegerField(min_value=1)
    email_request = forms.IntegerField(label="ID of the sent quotation email")
    sender = forms.EmailField(label="Registered account address that replied")
    cc = forms.EmailField(label="Copied purchasing contact or approver")
    signer_name = forms.CharField()
    role = forms.CharField()
    purchase_order_note = forms.CharField(label="Purchase order reference or no-PO statement")
    email_evidence = forms.FileField(label="Original email evidence")


class RentalReview(forms.Form):
    status = forms.ChoiceField(choices=[("confirmed","Confirmed by phone"),("rejected","Declined")])
    reason = forms.CharField(required=False, widget=forms.Textarea)


class VisitReview(forms.Form):
    action = forms.ChoiceField(choices=[("confirm","Confirm exact times"),("alternative","Offer another date"),("reject","Decline")])
    date = forms.DateField(required=False, widget=forms.DateInput(attrs={"type":"date"}), label="Alternative date")
    start = forms.DateTimeField(required=False, label="Confirmed start with time zone")
    end = forms.DateTimeField(required=False, label="Confirmed end with time zone")
    notes = forms.CharField(required=False, widget=forms.Textarea)


class Dispatch(forms.Form):
    technician_id = forms.IntegerField(label="Regional technician ID")
    purpose = forms.ChoiceField(choices=m.JobAssignment.Purpose.choices)
    instructions = forms.CharField(widget=forms.Textarea,required=False)
    reason = forms.CharField(required=False,label="Reason for reassignment")


class AnswerAssignment(forms.Form):
    status = forms.ChoiceField(choices=[("accepted","Accept"),("declined","Decline")])
    reason = forms.CharField(required=False,label="Reason if declined")


class Progress(forms.Form):
    status = forms.ChoiceField(choices=[(k,v) for k,v in m.JobEquipment.Status.choices if k in
        ("inspection","quotation","awaiting_approval","scheduled","awaiting_parts","in_progress","on_hold")])
    message = forms.CharField(widget=forms.Textarea)
    visibility = forms.ChoiceField(choices=m.Visibility.choices)
    estimated_completion = forms.DateTimeField(required=False)


TASKS = {
    "assign": (m.JobEquipment,"assign_technician","Assign technician",Dispatch,dispatch.assign,{}),
    "assignment-respond": (m.JobAssignment,"respond_to_assignment","Respond to assignment",AnswerAssignment,dispatch.respond,{}),
    "service-progress": (m.JobEquipment,"add_serviceupdate","Record service progress",Progress,dispatch.progress,{}),
    "invoice-credit": (m.Invoice, "add_creditnote","Issue a credit note", Credit, ledger.issue_credit_note, {}),
    "invoice-pay": (m.Invoice, "add_payment","Record payment", Pay, operations.record_payment, {}),
    "invoice-void": (m.Invoice, "change_invoice","Void invoice", Reason, operations.void_entry, {"kind":"invoice"}),
    "payment-void": (m.Payment, "change_payment","Void payment entry", Reason, operations.void_entry, {"kind":"payment"}),
    "invoice-issue": (m.Invoice, "change_invoice","Issue invoice and generate its PDF", IssueDocument, operations.issue, {"kind":"invoice"}),
    "quotation-issue": (m.Quotation,"change_quotation","Send quotation and generate its PDF",IssueQuote,operations.issue,{"kind":"quotation"}),
    "quotation-reply": (m.Quotation,"verify_quotation_reply","Verify a quotation reply", QuoteReply, decisions.verify_reply, {}),
    "report-return": (m.EmailApprovalSubmission,"verify_email_approval","Verify signed report PDF",forms.Form,decisions.verify_return,{}),
    "rental-review": (m.RentalOrder,"change_rentalorder","Record rental outcome",RentalReview,rentals.review,{}),
    "visit-review": (m.RescheduleRequest,"change_reschedulerequest","Review reschedule request",VisitReview,schedule.review,{}),
}
for kind, (model, _) in publication.SOURCES.items():
    TASKS[kind+"-publish"] = (model,"add_generateddocument","Publish PDF",forms.Form,publication.publish_view,{"kind":kind})
for kind, (model, _) in decisions.REPORTS.items():
    perm = "change_"+model._meta.model_name
    for name, title, form, view in [
        ("tablet","Record on-site approval",Tablet,decisions.tablet),
        ("sign","Record technician signature",TechnicianSignature,decisions.technician_signature),
        ("submit","Submit this version for approval",forms.Form,operations.submit_report),
    ]:
        TASKS[kind+"-"+name] = (model,perm,title,form,view,{"kind":kind})


def workflow(request, task, pk):
    from django.urls import reverse
    if task not in TASKS:
        raise Http404
    model, permission, title, form_class, view, kwargs = TASKS[task]
    if not request.user.has_perm("web."+permission):
        raise PermissionDenied()
    obj = get_object_or_404(scoped(model.objects.all(),request.user),pk=pk)
    initial = {"version":getattr(obj,"version",None)}
    form = form_class(request.POST if request.method=="POST" else None, request.FILES if request.method=="POST" else None, initial=initial)
    if request.method == "POST" and form.is_valid():
        # Use the same authenticated, transactional handler as the tablet/API.
        # Django forms send blank optional fields. DRF should see them as omitted.
        post = request.POST.copy()
        for key in form.fields:
            if post.get(key) == "" and not form.fields[key].required:
                post.pop(key, None)
        request.POST = post
        response = view(request, pk=pk, **kwargs)
        if response.status_code < 300:
            messages.success(request, title+" completed.")
            return redirect(reverse("admin:web_"+model._meta.model_name+"_changelist"))
        data = response.data
        error = data.get("message") or data.get("detail") if isinstance(data,dict) else data
        form.add_error(None, str(error or data))
    evidence = []
    if task == "report-return":
        evidence = [(link("pdf",obj.source_document_id,"file"),"Original PDF"),
                    (link("return",obj.pk,"signed_pdf"),"Returned signed PDF"),
                    (link("return",obj.pk,"email_evidence"),"Original email")]
    return render(request,"admin/portal_operation.html",
                  {**admin.site.each_context(request),"title":title,"object":obj,"form":form,"evidence":evidence})
