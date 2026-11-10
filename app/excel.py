"""Formatted Excel (.xlsx) export of the monthly muster roll and summary."""
import io

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from .attendance import fmt_minutes

FILLS = {
    "P": "E5F5EC", "A": "FDE8E8", "HD": "FFF4DC", "MP": "FFF4DC", "L": "E7F0FC", "H": "EEF0F4", "WO": "EEF0F4",
}
HEAD = PatternFill("solid", fgColor="161B26")
THIN = Side(style="thin", color="D5DAE3")
BOX = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)


def _text(value):
    """Keep user text as text (no formulas)."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@"):
        return "'" + value
    return value


def _header(ws, row, values):
    for col, value in enumerate(values, start=1):
        c = ws.cell(row=row, column=col, value=value)
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = HEAD
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        c.border = BOX


def monthly_workbook(rows, days, title, company):
    wb = Workbook()

    # --- Muster roll -----------------------------------------------------
    ws = wb.active
    ws.title = "Muster roll"
    ws.cell(row=1, column=1, value=f"{company} - Muster roll - {title}").font = Font(bold=True, size=14)
    head = ["Emp No", "Name", "Department"] + [f"{d.day}\n{d:%a}"[:6] for d in days] + \
           ["Present", "Absent", "Leave", "Holiday", "Weekly off", "Late", "Payable"]
    _header(ws, 3, head)
    for r, (emp, ds, s) in enumerate(rows, start=4):
        values = [_text(emp["employee_no"]), _text(emp["name"]), _text(emp["department"] or "")]
        values += [d.status if d.status != "-" else "" for d in ds]
        values += [s.present, s.absent, s.leave, s.holiday, s.weekly_off, s.late, s.payable]
        for col, value in enumerate(values, start=1):
            c = ws.cell(row=r, column=col, value=value)
            c.border = BOX
            if 4 <= col < 4 + len(ds):
                c.alignment = Alignment(horizontal="center")
                d = ds[col - 4]
                if d.status in FILLS:
                    c.fill = PatternFill("solid", fgColor=FILLS[d.status])
                if d.late_minutes:
                    c.font = Font(color="C62828", bold=True)
    ws.column_dimensions["A"].width = 9
    ws.column_dimensions["B"].width = 24
    ws.column_dimensions["C"].width = 16
    for col in range(4, 4 + len(days)):
        ws.column_dimensions[get_column_letter(col)].width = 5
    for col in range(4 + len(days), 4 + len(days) + 7):
        ws.column_dimensions[get_column_letter(col)].width = 9
    ws.row_dimensions[3].height = 30
    ws.freeze_panes = "D4"
    legend = len(rows) + 5
    ws.cell(row=legend, column=2, value="P Present, A Absent, HD Half day, MP Missing punch, L Leave, "
                                        "H Holiday, WO Weekly off. Red = late arrival.").font = Font(italic=True)

    # --- Summary ---------------------------------------------------------
    ws2 = wb.create_sheet("Summary")
    ws2.cell(row=1, column=1, value=f"{company} - Attendance summary - {title}").font = Font(bold=True, size=14)
    head2 = ["Emp No", "Name", "Department", "Present", "Absent", "Half day", "Missing punch", "Leave",
             "Unpaid leave", "Holiday", "Weekly off", "Late days", "Early exits", "Work hours", "OT hours",
             "Payable days"]
    _header(ws2, 3, head2)
    for r, (emp, _, s) in enumerate(rows, start=4):
        values = [_text(emp["employee_no"]), _text(emp["name"]), _text(emp["department"] or ""), s.present,
                  s.absent, s.half_day, s.missing_punch, s.leave, s.unpaid_leave, s.holiday, s.weekly_off, s.late,
                  s.early, fmt_minutes(s.work_minutes) or "0:00", fmt_minutes(s.ot_minutes) or "0:00", s.payable]
        for col, value in enumerate(values, start=1):
            c = ws2.cell(row=r, column=col, value=value)
            c.border = BOX
        ws2.cell(row=r, column=len(values)).font = Font(bold=True)
    for col, width in enumerate([9, 24, 16] + [11] * 13, start=1):
        ws2.column_dimensions[get_column_letter(col)].width = width
    ws2.row_dimensions[3].height = 30
    ws2.freeze_panes = "D4"

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
