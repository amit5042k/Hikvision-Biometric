"""Formatted Excel (.xlsx) export of registers."""
import io

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

FILLS = {"P": "E5F5EC", "A": "FDE8E8", "H": "EEF0F4", "WO": "EEF0F4"}
HEAD = PatternFill("solid", fgColor="161B26")
THIN = Side(style="thin", color="D5DAE3")
BOX = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)


def _text(value):
    """Keep user text as text (no formulas)."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@"):
        return "'" + value
    return value


def table_workbook(title, header, rows, school, fills=None):
    """A single formatted sheet. ``fills=(first, end)`` colours status marks in those columns (0-based)."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Register"
    ws.cell(row=1, column=1, value=f"{school} - {title}").font = Font(bold=True, size=14)
    for col, value in enumerate(header, start=1):
        c = ws.cell(row=3, column=col, value=value)
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = HEAD
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        c.border = BOX
    for r, values in enumerate(rows, start=4):
        for col, value in enumerate(values, start=1):
            c = ws.cell(row=r, column=col, value=_text(value))
            c.border = BOX
            if fills and fills[0] <= col - 1 < fills[1]:
                c.alignment = Alignment(horizontal="center")
                if value in FILLS:
                    c.fill = PatternFill("solid", fgColor=FILLS[value])
    for col, name in enumerate(header, start=1):
        narrow = fills and fills[0] <= col - 1 < fills[1]
        ws.column_dimensions[get_column_letter(col)].width = 5 if narrow else max(10, min(28, len(str(name)) + 4))
    ws.column_dimensions["B"].width = 24
    ws.row_dimensions[3].height = 30
    ws.freeze_panes = "C4"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
