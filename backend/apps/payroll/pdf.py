"""The payslip as a PDF, laid out like the approved template (A4, one page)."""
from decimal import Decimal
from io import BytesIO
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph, Table, TableStyle
from reportlab.lib.styles import ParagraphStyle

INK = colors.HexColor("#1f2a24")
MUTED = colors.HexColor("#4d5a52")
RULE = colors.HexColor("#c9d1cc")
TINT = colors.HexColor("#f1f4f2")
MARGIN = 16 * mm
WIDTH, HEIGHT = A4
USABLE = WIDTH - 2 * MARGIN


def money(value) -> str:
    return f"£{Decimal(value):,.2f}"


def hours(minutes) -> str:
    return f"{Decimal(minutes) / 60:.2f}"


def style(size=9, bold=False, colour=INK, align=0, leading=None):
    return ParagraphStyle(
        "s", fontName="Helvetica-Bold" if bold else "Helvetica", fontSize=size, textColor=colour,
        alignment=align, leading=leading or size * 1.3,
    )


def text(value, **kw):
    return Paragraph(escape(str(value)).replace("\n", "<br/>"), style(**kw))


def table(rows, widths, total=False, header=True):
    t = Table(rows, colWidths=widths)
    commands = [
        ("FONT", (0, 0), (-1, -1), "Helvetica", 8.5), ("TEXTCOLOR", (0, 0), (-1, -1), INK),
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LINEBELOW", (0, 0), (-1, -1), 0.6, RULE), ("TOPPADDING", (0, 0), (-1, -1), 3.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
    ]
    if header:
        commands += [
            ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 7.5), ("BACKGROUND", (0, 0), (-1, 0), TINT),
            ("LINEBELOW", (0, 0), (-1, 0), 1.2, INK),
        ]
    if total:
        commands += [("FONT", (0, -1), (-1, -1), "Helvetica-Bold", 8.5), ("BACKGROUND", (0, -1), (-1, -1), TINT),
                     ("LINEBELOW", (0, -1), (-1, -1), 1.2, INK)]
    t.setStyle(TableStyle(commands))
    return t


def place(pdf, flowable, x, top, width):
    """Draw a flowable with its top edge at `top`; return the y of its bottom edge."""
    _, height = flowable.wrapOn(pdf, width, HEIGHT)
    flowable.drawOn(pdf, x, top - height)
    return top - height


def render(slip, template, details, ytd, logo_bytes=None):
    """`slip` is a Payslip; `template` the wording dict stored with the run; `details` masked pay details."""
    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    pdf.setTitle(f"Payslip {slip.number}")
    pdf.setAuthor(template.get("trading_name") or "")
    accent = colors.HexColor(template.get("accent_colour") or "#1d5c3a")
    run = slip.pay_run
    y = HEIGHT - MARGIN

    # Header: logo and employer on the left, title on the right.
    left = MARGIN
    if logo_bytes:
        try:
            image = ImageReader(BytesIO(logo_bytes))
            w, h = image.getSize()
            scale = min(38 * mm / w, 16 * mm / h)
            pdf.drawImage(image, left, y - h * scale, w * scale, h * scale, mask="auto")
            left += 38 * mm + 4 * mm
        except Exception:  # a damaged logo must not stop payslips being made
            pass
    lines = [template.get("legal_employer_name", ""), template.get("employer_address", "")]
    if template.get("paye_reference"):
        lines.append(f"PAYE ref: {template['paye_reference']}")
    block_top = y
    block_top = place(pdf, text(template.get("trading_name", ""), size=12, bold=True, colour=accent), left, block_top, 100 * mm)
    place(pdf, text("\n".join(l for l in lines if l), size=8, colour=MUTED), left, block_top, 100 * mm)
    pdf.setFont("Helvetica-Bold", 22)
    pdf.setFillColor(INK)
    pdf.drawRightString(WIDTH - MARGIN, y - 18, "PAYSLIP")
    pdf.setFont("Helvetica", 8)
    pdf.setFillColor(MUTED)
    pdf.drawRightString(WIDTH - MARGIN, y - 30, f"Payslip no. {slip.number}")
    y -= 24 * mm

    # Employee box.
    store = slip.store.name if slip.store_id else ""
    cells = [
        ("Employee", slip.employee.user.get_full_name() or slip.employee.employee_number),
        ("Employee no.", slip.employee.employee_number),
        ("NI number", details.get("ni_number") or "-"),
        ("Tax code", details.get("tax_code") or "-"),
        ("Pay period", f"{run.period_start:%d %b} - {run.period_end:%d %b %Y}"),
        ("Pay date", f"{run.pay_date:%a %d %b %Y}"),
        ("Tax week", f"{ytd['tax_week']} ({ytd['tax_year']})"),
        ("Store", store),
    ]
    rows = [
        [Paragraph(f"<font size=6.5 color='#4d5a52'>{escape(l.upper())}</font><br/><b>{escape(str(v))}</b>", style(8.5)) for l, v in cells[i:i + 4]]
        for i in (0, 4)
    ]
    box = Table(rows, colWidths=[USABLE / 4] * 4)
    box.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.8, RULE), ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f8faf9")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    y = place(pdf, box, MARGIN, y, USABLE) - 6 * mm

    # Hours.
    y = place(pdf, text("HOURS WORKED  (from your approved rota and clock records)", size=8, bold=True), MARGIN, y, USABLE) - 2 * mm
    day_rows = [["Day", "Rota", "Clocked", "Break taken", "Unpaid break", "Paid hours"]]
    from datetime import date as date_type

    for d in sorted(slip.days, key=lambda r: (r["date"], r["clocked"])):
        shown = date_type.fromisoformat(d["date"])
        day_rows.append([
            f"{shown:%a %d %b}", d["rota"] or "-", d["clocked"], f"{d['break_taken']} min",
            f"{d['unpaid_break']} min", hours(d["paid_minutes"]),
        ])
    if len(day_rows) == 1:
        day_rows.append(["No hours paid in this run", "", "", "", "", "0.00"])
    day_rows.append(["Total", "", "", "", f"{slip.unpaid_break_minutes} min", hours(slip.paid_minutes)])
    y = place(pdf, table(day_rows, [30 * mm, 30 * mm, 30 * mm, 30 * mm, 30 * mm, USABLE - 150 * mm], total=True), MARGIN, y, USABLE) - 1.5 * mm
    y = place(pdf, text(
        "Breaks are paid up to your allowance (30 min for shifts of 6 to 12 hours, 60 min over 12 hours). Time beyond it is unpaid.",
        size=7.5, colour=MUTED), MARGIN, y, USABLE) - 1.5 * mm
    if slip.held:
        y = place(pdf, text(
            f"{len(slip.held)} shift(s) are not paid yet because they are waiting for a check or a rate. They will be paid in a later payslip once cleared.",
            size=7.5, colour=MUTED), MARGIN, y, USABLE) - 1.5 * mm
    y -= 4 * mm

    # Earnings (left) and deductions (right).
    gap = 8 * mm
    column = (USABLE - gap) / 2
    earn = [["Item", "Hours", "Rate", "Amount"]]
    for line in slip.earnings:
        if line["rate"]:
            earn.append([line["label"], hours(line["minutes"]), money(line["rate"]), money(line["amount"])])
        else:
            earn.append([line["label"], "-", "-", money(line["amount"])])
    earn.append(["Gross pay", "", "", money(slip.gross_pay)])
    ded = [
        ["Item", "This pay", "Year to date"],
        ["Income tax (PAYE)", money(slip.income_tax), money(ytd["income_tax"])],
        ["National Insurance", money(slip.national_insurance), money(ytd["national_insurance"])],
        ["Workplace pension", money(slip.pension), money(ytd["pension"])],
        ["Student loan", money(slip.student_loan), money(ytd["student_loan"])],
        ["Other", money(slip.other_deductions), money(ytd["other_deductions"])],
        ["Total deductions", money(slip.total_deductions), money(ytd["total_deductions"])],
    ]
    top = y
    pdf.setFont("Helvetica-Bold", 8)
    pdf.setFillColor(INK)
    pdf.drawString(MARGIN, top - 8, "EARNINGS")
    pdf.drawString(MARGIN + column + gap, top - 8, "DEDUCTIONS")
    end_left = place(pdf, table(earn, [column * 0.34, column * 0.2, column * 0.22, column * 0.24], total=True), MARGIN, top - 12, column)
    end_right = place(pdf, table(ded, [column * 0.46, column * 0.27, column * 0.27], total=True), MARGIN + column + gap, top - 12, column)
    y = min(end_left, end_right) - 6 * mm

    # Year to date.
    ytd_cells = [("Gross pay to date", ytd["gross_pay"]), ("Tax paid to date", ytd["income_tax"]), ("Net pay to date", ytd["net_pay"])]
    ytd_rows = [[Paragraph(f"<font size=6.5 color='#4d5a52'>{escape(l.upper())}</font><br/><b>{money(v)}</b>", style(9)) for l, v in ytd_cells]]
    ytd_box = Table(ytd_rows, colWidths=[USABLE / 3] * 3)
    ytd_box.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.8, RULE), ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]))
    y = place(pdf, ytd_box, MARGIN, y, USABLE) - 6 * mm

    # Net pay band.
    band = 20 * mm
    pdf.setFillColor(accent)
    pdf.rect(MARGIN, y - band, USABLE, band, stroke=0, fill=1)
    pdf.setFillColor(colors.white)
    pdf.setFont("Helvetica", 8)
    pdf.drawString(MARGIN + 5 * mm, y - 7 * mm, "NET PAY")
    if template.get("show_bank_line", True) and details.get("has_bank_details"):
        pdf.setFont("Helvetica", 8)
        reference = f"WAGES {run.pay_date:%d%b}".upper()
        pdf.drawString(
            MARGIN + 5 * mm, y - 13 * mm,
            f"Paid to account ending {details['account_number'][-4:]}, sort code {details['sort_code']}, ref {reference}",
        )
    pdf.setFont("Helvetica-Bold", 22)
    pdf.drawRightString(WIDTH - MARGIN - 5 * mm, y - 12 * mm, money(slip.net_pay))
    y -= band + 6 * mm

    # Footer.
    foot = []
    if template.get("hr_email"):
        foot.append(f"Questions about your pay? Email {template['hr_email']}.")
    if template.get("footer_note"):
        foot.append(template["footer_note"])
    employer = " ".join(p for p in [
        f"Employer: {template['legal_employer_name']}." if template.get("legal_employer_name") else "",
        f"Company no.: {template['company_number']}." if template.get("company_number") else "",
    ] if p)
    if employer:
        foot.append(employer)
    place(pdf, text("\n".join(foot), size=7.5, colour=MUTED), MARGIN, y, USABLE)
    pdf.showPage()
    pdf.save()
    return buffer.getvalue()
