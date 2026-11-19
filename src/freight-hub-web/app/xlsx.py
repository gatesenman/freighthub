"""最小化的 .xlsx 读写（只用标准库 zipfile + xml，打包版不需要 openpyxl）。

write_xlsx(sheets) → bytes：sheets = [{"name", "rows": [[...]], "widths": [..], "header": True, "validations": [(range, [options])]}]
read_xlsx(bytes) → [(sheet_name, [[str, ...], ...])]
"""
import io
import re
import zipfile
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
      "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}
_BAD = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _col(n):  # 0 → A
    s = ""
    n += 1
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def _cell(ref, v, style):
    v = "" if v is None else str(v)
    v = _BAD.sub("", v)
    if v == "":
        return f'<c r="{ref}" s="{style}"/>' if style else ""
    return f'<c r="{ref}" t="inlineStr" s="{style}"><is><t xml:space="preserve">{escape(v)}</t></is></c>'


def _sheet_xml(sh):
    rows = sh.get("rows") or []
    widths = sh.get("widths") or []
    out = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
           f'<worksheet xmlns="{NS["m"]}">']
    if sh.get("header", True):
        out.append('<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>')
    if widths:
        out.append("<cols>" + "".join(f'<col min="{i + 1}" max="{i + 1}" width="{w}" customWidth="1"/>' for i, w in enumerate(widths)) + "</cols>")
    out.append("<sheetData>")
    for ri, row in enumerate(rows):
        style = 1 if (ri == 0 and sh.get("header", True)) else (2 if sh.get("wrap") else 0)
        cells = "".join(_cell(f"{_col(ci)}{ri + 1}", v, style) for ci, v in enumerate(row))
        out.append(f'<row r="{ri + 1}">{cells}</row>')
    out.append("</sheetData>")
    vals = sh.get("validations") or []
    if vals:
        out.append(f'<dataValidations count="{len(vals)}">')
        for rng, opts in vals:
            f = escape('"' + ",".join(opts) + '"')
            out.append(f'<dataValidation type="list" allowBlank="1" showErrorMessage="0" sqref="{rng}"><formula1>{f}</formula1></dataValidation>')
        out.append("</dataValidations>")
    out.append("</worksheet>")
    return "".join(out)


STYLES = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
          f'<styleSheet xmlns="{NS["m"]}">'
          '<fonts count="2"><font><sz val="11"/><name val="Microsoft YaHei"/></font><font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="Microsoft YaHei"/></font></fonts>'
          '<fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill>'
          '<fill><patternFill patternType="solid"><fgColor rgb="FF0F6CBD"/><bgColor indexed="64"/></patternFill></fill></fills>'
          '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
          '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
          '<cellXfs count="3"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
          '<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"><alignment vertical="center"/></xf>'
          '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf></cellXfs>'
          '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>')


def write_xlsx(sheets):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        n = len(sheets)
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>'
                   '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                   '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
                   + "".join(f'<Override PartName="/xl/worksheets/sheet{i + 1}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' for i in range(n))
                   + "</Types>")
        z.writestr("_rels/.rels", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        z.writestr("xl/workbook.xml", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   f'<workbook xmlns="{NS["m"]}" xmlns:r="{NS["r"]}"><sheets>'
                   + "".join(f'<sheet name="{escape(s["name"])}" sheetId="{i + 1}" r:id="rId{i + 1}"/>' for i, s in enumerate(sheets))
                   + "</sheets></workbook>")
        z.writestr("xl/_rels/workbook.xml.rels", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   + "".join(f'<Relationship Id="rId{i + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i + 1}.xml"/>' for i in range(n))
                   + f'<Relationship Id="rId{n + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>')
        z.writestr("xl/styles.xml", STYLES)
        for i, s in enumerate(sheets):
            z.writestr(f"xl/worksheets/sheet{i + 1}.xml", _sheet_xml(s))
    return buf.getvalue()


def _ref_col(ref):
    m = re.match(r"([A-Z]+)", ref or "")
    n = 0
    for ch in m.group(1) if m else "A":
        n = n * 26 + ord(ch) - 64
    return n - 1


def _num(txt):
    try:
        f = float(txt)
        if f == int(f) and abs(f) < 1e15 and "E" not in txt.upper():
            return str(int(f))
    except ValueError:
        pass
    return txt


def read_xlsx(data: bytes):
    z = zipfile.ZipFile(io.BytesIO(data))
    if sum(i.file_size for i in z.infolist()) > 80 * 1024 * 1024:   # 防 zip 炸弹
        raise ValueError("Excel 文件解压后过大，已拒绝")
    names = set(z.namelist())
    if "xl/workbook.xml" not in names:
        raise ValueError("不是有效的 .xlsx 文件")
    sst = []
    if "xl/sharedStrings.xml" in names:
        root = ET.fromstring(z.read("xl/sharedStrings.xml"))
        for si in root.findall("m:si", NS):
            sst.append("".join(t.text or "" for t in si.iter(f"{{{NS['m']}}}t")))
    wb = ET.fromstring(z.read("xl/workbook.xml"))
    rels = {}
    if "xl/_rels/workbook.xml.rels" in names:
        for r in ET.fromstring(z.read("xl/_rels/workbook.xml.rels")):
            rels[r.get("Id")] = r.get("Target")
    out = []
    for i, sh in enumerate(wb.find("m:sheets", NS)):
        rid = sh.get(f"{{{NS['r']}}}id")
        tgt = rels.get(rid) or f"worksheets/sheet{i + 1}.xml"
        path = tgt.lstrip("/") if tgt.startswith("/") else "xl/" + tgt
        if path not in names:
            continue
        root = ET.fromstring(z.read(path))
        rows = []
        for row in root.iter(f"{{{NS['m']}}}row"):
            cells = {}
            for c in row.findall("m:c", NS):
                t = c.get("t")
                if t == "inlineStr":
                    v = "".join(x.text or "" for x in c.iter(f"{{{NS['m']}}}t"))
                else:
                    ve = c.find("m:v", NS)
                    v = ve.text if ve is not None and ve.text is not None else ""
                    if t == "s" and v != "":
                        v = sst[int(v)] if int(v) < len(sst) else ""
                    elif t in (None, "n") and v != "":
                        v = _num(v)
                    elif t == "b":
                        v = "是" if v == "1" else "否"
                col = _ref_col(c.get("r"))
                if col > 500:      # 异常列号（如 ZZZZZZ1）会让下面的补齐列表无限膨胀
                    continue
                cells[col] = v
            if cells:
                m = max(cells) + 1
                rows.append([cells.get(k, "") for k in range(m)])
            else:
                rows.append([])
        out.append((sh.get("name"), rows))
    return out
