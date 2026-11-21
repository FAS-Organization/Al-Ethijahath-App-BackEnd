from collections import defaultdict
from decimal import Decimal
from io import BytesIO
from xml.sax.saxutils import escape

from django.http import HttpResponse
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from rest_framework.decorators import api_view, permission_classes
from .permissions import IsCustomer
from .records import invoices_for


def render_pdf(title,paragraphs,rows,signatures=()):
    import os
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    font="Helvetica"
    if os.getenv("PORTAL_PDF_FONT"):
        font="PortalBody"
        if font not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(font,os.environ["PORTAL_PDF_FONT"]))
    out=BytesIO()
    body=ParagraphStyle("Body",fontName=font,fontSize=10,leading=14,spaceAfter=5)
    head=ParagraphStyle("Title",parent=body,fontSize=17,leading=21,textColor=colors.HexColor("#861a25"),spaceAfter=10)
    def p(text,style=body):
        return Paragraph(escape(str(text if text is not None else "")).replace("\n","<br/>"),style)
    flow=[p(title,head)]+[p(s) for s in paragraphs]
    if rows:
        width=(A4[0]-36*mm-12)/len(rows[0])
        table=Table([[p(s) for s in r] for r in rows],colWidths=[width]*len(rows[0]),repeatRows=1,splitByRow=1,splitInRow=1)
        table.setStyle(TableStyle([("VALIGN",(0,0),(-1,-1),"TOP"),("BACKGROUND",(0,0),(-1,0),colors.HexColor("#f7eeea")),
                                   ("LINEBELOW",(0,0),(-1,-1),.35,colors.lightgrey)]))
        flow.extend([Spacer(1,4*mm),table])
    for caption,file in signatures:
        with file.open("rb") as source:
            stream=BytesIO(source.read())
        w,h=ImageReader(stream).getSize()
        scale=min(40*mm/w,18*mm/h)
        flow.extend([Spacer(1,3*mm),p(caption),Image(stream,width=w*scale,height=h*scale)])
    SimpleDocTemplate(out,pagesize=A4,leftMargin=18*mm,rightMargin=18*mm,title=title).build(flow)
    return out.getvalue()


@api_view(["GET"])
@permission_classes([IsCustomer])
def statement(request):
    c=request.user.customer_company
    entries=[]
    totals=defaultdict(lambda:Decimal("0.00"))
    for i in invoices_for(c):
        if i["status"]!="issued":
            continue
        curr=i["currency"]
        totals[curr]+=Decimal(i["balance_due"])
        entries.append([str(i["issue_date"]),i["number"],"Invoice",curr,i["total"]])
        for pay in i["payments"]:
            entries.append([str(pay["paid_at"].date()),i["number"],"Payment",curr,"-"+pay["amount"]])
        for note in i["credit_notes"]:
            entries.append([str(note["issued_at"])[:10],i["number"],"Credit "+note["number"],curr,"-"+note["amount"]])
    rows=[["Date","Invoice","Entry","Currency","Amount"]]+sorted(entries)
    data=render_pdf("Statement of account",[c.name]+[f"Outstanding: {currency} {n:.2f}" for currency,n in sorted(totals.items())],rows)
    response=HttpResponse(data,content_type="application/pdf")
    response["Content-Disposition"]='attachment; filename="statement-of-account.pdf"'
    response["Cache-Control"]="private, no-store"
    return response
