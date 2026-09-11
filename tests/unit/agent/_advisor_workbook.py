from __future__ import annotations

import re
from pathlib import Path
from xml.etree import ElementTree
from zipfile import ZipFile

from agent.advisor_profile_matcher import AdvisorClientTypeDefinition
from utils.client_types import CLIENT_TYPE_CODE_COLUMN


SPREADSHEET_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
NS = {"x": SPREADSHEET_NS}


def _column_index(cell_reference: str) -> int:
    letters = re.match(r"[A-Z]+", cell_reference)
    assert letters is not None
    value = 0
    for letter in letters.group(0):
        value = value * 26 + ord(letter) - ord("A") + 1
    return value - 1


def _shared_strings(archive: ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
    return [
        "".join(node.text or "" for node in item.findall(".//x:t", NS))
        for item in root.findall("x:si", NS)
    ]


def read_first_xlsx_sheet(path: Path) -> list[dict[str, object]]:
    with ZipFile(path) as archive:
        shared_strings = _shared_strings(archive)
        root = ElementTree.fromstring(archive.read("xl/worksheets/sheet1.xml"))

    rows: list[list[object]] = []
    for row_element in root.findall(".//x:sheetData/x:row", NS):
        values: list[object] = []
        for cell in row_element.findall("x:c", NS):
            column = _column_index(cell.attrib["r"])
            while len(values) <= column:
                values.append(None)
            cell_type = cell.attrib.get("t")
            raw = cell.findtext("x:v", default="", namespaces=NS)
            if cell_type == "s":
                value: object = shared_strings[int(raw)]
            elif cell_type == "inlineStr":
                value = "".join(
                    node.text or "" for node in cell.findall(".//x:t", NS)
                )
            elif cell_type == "b":
                value = raw == "1"
            elif raw:
                numeric = float(raw)
                value = int(numeric) if numeric.is_integer() else numeric
            else:
                value = None
            values[column] = value
        rows.append(values)

    headers = [str(value) for value in rows[0]]
    result: list[dict[str, object]] = []
    for values in rows[1:]:
        padded = values + [None] * (len(headers) - len(values))
        row = dict(zip(headers, padded[: len(headers)]))
        if any(value is not None and str(value).strip() for value in row.values()):
            result.append(row)
    return result


def client_type_workbook_rows(path: Path) -> list[dict[str, object]]:
    rows = read_first_xlsx_sheet(path)
    data_rows = [
        row
        for row in rows
        if row.get("profile_name")
        and row.get("profile_name") != "Тип профиля"
    ]
    result: list[dict[str, object]] = []
    for index, row in enumerate(data_rows, start=1):
        result.append(
            {
                CLIENT_TYPE_CODE_COLUMN: f"CT-{index:03d}",
                **row,
            }
        )
    return result


def load_client_type_definitions(path: Path) -> list[AdvisorClientTypeDefinition]:
    return [
        AdvisorClientTypeDefinition.from_mapping(row)
        for row in client_type_workbook_rows(path)
    ]
