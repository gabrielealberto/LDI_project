"""Small helpers for exporting a single web-table view as an Excel file."""

from __future__ import annotations

from io import BytesIO

import numpy as np
import pandas as pd
from flask import send_file


EXCEL_MIMETYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def excel_download(frame: pd.DataFrame, filename: str, sheet_name: str):
    """Return an in-memory, formatted workbook for one visible web table."""
    output = BytesIO()
    export = frame.replace([np.inf, -np.inf], np.nan)
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        export.to_excel(writer, sheet_name=sheet_name, index=False)
        sheet = writer.sheets[sheet_name]
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions
        for column_cells in sheet.columns:
            values = [
                "" if cell.value is None else str(cell.value) for cell in column_cells
            ]
            sheet.column_dimensions[column_cells[0].column_letter].width = min(
                max(max(map(len, values)) + 2, 12), 36
            )
    output.seek(0)
    return send_file(
        output,
        as_attachment=True,
        download_name=filename,
        mimetype=EXCEL_MIMETYPE,
    )
