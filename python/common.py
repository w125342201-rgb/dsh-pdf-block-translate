# -*- coding: utf-8 -*-
"""Layout analysis shared by prepare/finish.

Segments a PDF into semantic paragraphs, classifies them (body / caption /
equation / reference / meta / table cell), picks a representative block per
paragraph, and computes a same-page union bbox.  The output is deliberately
simple JSON so the agent only edits `zh` strings.
"""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

import fitz

MATH = set(
    "αβγδεζηθικλμνξοπρστυφχψω"
    "ΑΒΓΔΕΖΗΘΙΚΛΜΝΞΟΠΡΣΤΥΦΧΨΩ"
    "∂∇√∫∑∞≈≠≤≥±×·−→←↑↓⇒⇔∈∉⊂⊃∪∩⊗⊕"
)
REF_PAT = re.compile(r"^\s*(\[\d+\]|\d{1,3}[\.\)])\s")
CAP_PAT = re.compile(r"^(Figure|Fig\.?|Table|图|表)\s*\d", re.I)
META_PAT = re.compile(
    r"(Laboratoire|University|Institute|Department|CNRS|ESPCI|CSNSM|"
    r"Universit|Institut|Academy|College|School of)", re.I)
STOP_PAT = re.compile(
    r"^(Acknowledg|致谢|Competing|Correspondence|Table\s*\d|Figure\s*\d|"
    r"图\s*\d|表\s*\d)", re.I)
EQ_NUM = re.compile(r"^\(\d+[a-z]?\)$")

# Tunable parameters live in profiles/<id>.json under "params".  evolve.py
# adjusts this dict after replaying stored cases; prepare.py loads it.
DEFAULT_PARAMS = {
    "line_anchor_tol": 6.0,
    "same_row_gap": 48.0,
    "col_wide_ratio": 0.55,
    "col_min_lines": 8,
    "para_gap_factor": 0.9,
    "para_gap_extra": 2.0,
    "indent_pt": 6.0,
    "formula_line_ratio": 0.55,
    "formula_line_len": 130,
    "eq_ratio": 0.30,
    "eq_max_width": 0.82,
    "eq_max_len": 260,
    "eq_merge_gap": 8.0,
    "clean_formula_ratio": 0.18,
    "clean_frag_max": 4,
    "clean_min_words": 6,
}
P = dict(DEFAULT_PARAMS)


def set_params(params: dict | None) -> None:
    P.clear()
    P.update(DEFAULT_PARAMS)
    P.update(params or {})


def formula_ratio(text: str) -> float:
    if not text:
        return 0.0
    n = sum(1 for c in text if c in MATH or 0x1D400 <= ord(c) < 0x1D800
            or 0x2200 <= ord(c) <= 0x22FF)
    return n / len(text)


def digit_ratio(text: str) -> float:
    if not text:
        return 0.0
    n = sum(1 for c in text if c.isdigit() or c in ".,;:+-/%()[]<>=")
    return n / len(text)


def _raw_rows(page) -> list[dict]:
    rows = []
    for blk in page.get_text("dict").get("blocks", []):
        if blk.get("type"):
            continue
        for ln in blk.get("lines", []):
            spans = [s for s in ln.get("spans", []) if s["text"].strip()]
            if not spans:
                continue
            text = "".join(s["text"] for s in ln.get("spans", []))
            size = max(s["size"] for s in spans)
            bold_chars = sum(len(s["text"]) for s in spans
                             if (s.get("flags", 0) & 16) or "Bold" in s.get("font", ""))
            rows.append({"bbox": [float(v) for v in ln["bbox"]], "text": text,
                         "size": float(size), "bold": bold_chars / max(len(text), 1) > 0.6})
    return rows


def _same_visual_row(a: dict, b: dict, page_w: float) -> bool:
    ca = (a["bbox"][1] + a["bbox"][3]) / 2
    cb = (b["bbox"][1] + b["bbox"][3]) / 2
    if abs(ca - cb) > P["line_anchor_tol"] + 1.5:
        return False
    ha = a["bbox"][2] - a["bbox"][0]
    hb = b["bbox"][2] - b["bbox"][0]
    if ha < 0.45 * page_w and hb < 0.45 * page_w:
        if abs(a["bbox"][0] - b["bbox"][0]) > 0.30 * page_w:
            return False
    gap = (b["bbox"][0] - a["bbox"][2]) if b["bbox"][0] >= a["bbox"][0] else (a["bbox"][0] - b["bbox"][2])
    return gap < P["same_row_gap"]


def build_lines(page, page_w: float) -> list[dict]:
    rows = _raw_rows(page)
    if not rows:
        return []
    rows.sort(key=lambda r: ((r["bbox"][1] + r["bbox"][3]) / 2, r["bbox"][0]))
    clusters: list[dict] = []
    for r in rows:
        placed = False
        cy = (r["bbox"][1] + r["bbox"][3]) / 2
        for c in clusters:
            if abs(c["cy"] - cy) <= P["line_anchor_tol"] and _same_visual_row(c["last"], r, page_w):
                c["rows"].append(r)
                c["last"] = r
                c["bbox"] = [min(c["bbox"][0], r["bbox"][0]), min(c["bbox"][1], r["bbox"][1]),
                             max(c["bbox"][2], r["bbox"][2]), max(c["bbox"][3], r["bbox"][3])]
                placed = True
                break
        if not placed:
            clusters.append({"rows": [r], "last": r, "cy": cy, "bbox": list(r["bbox"])})
    lines = []
    for c in clusters:
        members = sorted(c["rows"], key=lambda r: r["bbox"][0])
        text = ""
        for i, m in enumerate(members):
            t = m["text"].strip()
            if not t:
                continue
            if not text:
                text = t
            else:
                gap = m["bbox"][0] - members[i - 1]["bbox"][2]
                text += (" " if gap > 2.5 else "") + t
        lines.append({"bbox": c["bbox"], "text": text,
                      "size": max(m["size"] for m in members),
                      "bold": any(m["bold"] for m in members)})
    lines.sort(key=lambda r: (r["bbox"][1], r["bbox"][0]))
    return lines


def detect_columns(lines: list[dict], page_w: float) -> str:
    if not lines:
        return "single"
    left = right = wide = 0
    for ln in lines:
        x0, x1 = ln["bbox"][0], ln["bbox"][2]
        if x1 - x0 > P["col_wide_ratio"] * page_w:
            wide += 1
        elif (x0 + x1) / 2 < page_w / 2:
            left += 1
        else:
            right += 1
    if left >= P["col_min_lines"] and right >= P["col_min_lines"] and wide < 0.5 * max(left + right, 1):
        return "two"
    return "single"


def column_of(ln: dict, page_w: float) -> int:
    x0, x1 = ln["bbox"][0], ln["bbox"][2]
    if x1 - x0 > 0.55 * page_w:
        return 2
    return 0 if (x0 + x1) / 2 < page_w / 2 else 1


def is_formula_line(ln: dict, text_width: float) -> bool:
    t = ln["text"].strip()
    if not t:
        return False
    if EQ_NUM.match(t):
        return True
    return formula_ratio(t) >= P["formula_line_ratio"] and len(t) <= P["formula_line_len"]


def equation_regions(page, page_w: float) -> list[list[float]]:
    """Merge high-formula text blocks into display-equation regions."""
    cands = []
    for blk in page.get_text("dict").get("blocks", []):
        if blk.get("type"):
            continue
        text = "".join(s["text"] for ln in blk.get("lines", [])
                       for s in ln.get("spans", []))
        if not text.strip():
            continue
        x0, y0, x1, y1 = blk["bbox"]
        if x1 - x0 > P["eq_max_width"] * page_w or len(text) > P["eq_max_len"]:
            continue
        if formula_ratio(text) < P["eq_ratio"]:
            continue
        cands.append([float(x0), float(y0), float(x1), float(y1)])
    merged: list[list[float]] = []
    for r in cands:
        placed = False
        for m in merged:
            g = P["eq_merge_gap"]
            if (min(m[2], r[2]) - max(m[0], r[0]) > -g
                    and min(m[3], r[3]) - max(m[1], r[1]) > -g):
                m[0], m[1] = min(m[0], r[0]), min(m[1], r[1])
                m[2], m[3] = max(m[2], r[2]), max(m[3], r[3])
                placed = True
                break
        if not placed:
            merged.append(list(r))
    return merged


def overlap_ratio(box: list[float], rect: list[float]) -> float:
    ix = min(box[2], rect[2]) - max(box[0], rect[0])
    iy = min(box[3], rect[3]) - max(box[1], rect[1])
    if ix <= 0 or iy <= 0:
        return 0.0
    area = max((box[2] - box[0]) * (box[3] - box[1]), 1.0)
    return ix * iy / area


def segment(lines: list[dict], page_w: float, page_h: float,
            columns: str, text_width: float) -> list[dict]:
    if not lines:
        return []
    groups: dict[int, list[dict]] = {}
    for ln in lines:
        c = column_of(ln, page_w) if columns == "two" else 0
        groups.setdefault(c, []).append(ln)
    paras: list[dict] = []
    for c, ls in groups.items():
        ls.sort(key=lambda r: (r["bbox"][1], r["bbox"][0]))
        xs = Counter(round(l["bbox"][0]) for l in ls)
        colmin = xs.most_common(1)[0][0] if xs else 0
        hs = sorted(l["bbox"][3] - l["bbox"][1] for l in ls)
        medh = hs[len(hs) // 2] if hs else 10.0
        cur: list[dict] = []

        def flush():
            nonlocal cur
            if cur:
                paras.append(_finish_para(cur, c))
                cur = []

        for ln in ls:
            if not cur:
                cur = [ln]
                continue
            p = cur[-1]
            gap = ln["bbox"][1] - p["bbox"][3]
            indent = ln["bbox"][0] > colmin + P["indent_pt"]
            same_row = abs(ln["bbox"][1] - p["bbox"][1]) <= 2
            if not same_row and (gap > medh * P["para_gap_factor"] + P["para_gap_extra"]
                                 or (indent and gap > 1)):
                flush()
                cur = [ln]
            else:
                cur.append(ln)
        flush()
    paras.sort(key=lambda p: (p["bbox"][1], p["bbox"][0]))
    return paras


def _finish_para(lines: list[dict], col: int, force_kind: str | None = None) -> dict:
    bbox = [min(l["bbox"][0] for l in lines), min(l["bbox"][1] for l in lines),
            max(l["bbox"][2] for l in lines), max(l["bbox"][3] for l in lines)]
    sizes = sorted(l["size"] for l in lines)
    size = sizes[len(sizes) // 2]
    bold_chars = sum(len(l["text"]) for l in lines if l["bold"])
    total = sum(len(l["text"]) for l in lines)
    text = ""
    for l in lines:
        t = l["text"].strip()
        if not t:
            continue
        if not text:
            text = t
        elif text.endswith("-") and t[:1].islower():
            text = text[:-1] + t
        else:
            text += " " + t
    return {"bbox": bbox, "text": text, "size": size,
            "bold": total > 0 and bold_chars / total > 0.6,
            "col": col, "formula": formula_ratio(text),
            "digits": digit_ratio(text), "force_kind": force_kind}


def _page_blocks(page) -> list[dict]:
    out = []
    for b in page.get_text("blocks"):
        if len(b) >= 7 and b[6] == 0 and b[4].strip():
            out.append({"bbox": [float(v) for v in b[:4]],
                        "text": b[4].replace("\n", " ").strip()})
    return out


def reading_text(pblocks: list[dict], par: dict) -> tuple[str, list[float]]:
    """Blocks mode keeps the content-stream reading order; use it to repair the
    word order that per-line x-sorting scrambles around inline formulas."""
    x0, y0, x1, y1 = par["bbox"]
    sel = []
    for b in pblocks:
        cx = (b["bbox"][0] + b["bbox"][2]) / 2
        cy = (b["bbox"][1] + b["bbox"][3]) / 2
        if x0 - 4 <= cx <= x1 + 4 and y0 - 5 <= cy <= y1 + 5:
            sel.append(b)
    if not sel:
        return par["text"], par["bbox"]
    text = " ".join(b["text"] for b in sel if b["text"])
    bb = [min(b["bbox"][0] for b in sel), min(b["bbox"][1] for b in sel),
          max(b["bbox"][2] for b in sel), max(b["bbox"][3] for b in sel)]
    return text, bb


def is_clean_prose(text: str) -> bool:
    """Conservative gate: translate only blocks whose text is not shredded by
    inline MathType fragments.  A false negative costs one untranslated block;
    a false positive produces scrambled Chinese, which is worse."""
    t = text.strip()
    if len(t) < 40:
        return False
    if formula_ratio(t) > P["clean_formula_ratio"]:
        return False
    frag = re.findall(r"(?:^|\s)([0-9α-ωΑ-Ω=+\-−×/√]{1,5})(?=\s|$)", t)
    if len(frag) >= P["clean_frag_max"]:
        return False
    words = re.findall(r"[A-Za-z]{2,}", t)
    if len(words) < P["clean_min_words"]:
        return False
    return True


def classify(par: dict, page_idx: int, page_h: float,
             text_width: float, colmin: float) -> str:
    t = par["text"].strip()
    if not t:
        return "empty"
    if par.get("force_kind"):
        return par["force_kind"]
    if par.get("region_equation"):
        return "equation"
    if re.fullmatch(r"\d{1,3}", t) and (par["bbox"][1] < 70 or par["bbox"][3] > page_h - 45):
        return "page_number"
    if CAP_PAT.match(t):
        return "caption" if is_clean_prose(t) else "formula_mixed"
    if REF_PAT.match(t) and re.search(r"(19|20)\d\d", t):
        return "reference"
    if page_idx == 0 and par["bbox"][1] < 165 and par["size"] >= 14:
        return "title"
    if page_idx == 0 and par["bbox"][1] < 285 and par["size"] >= 10:
        if META_PAT.search(t):
            return "meta"
        if t.count(",") >= 3 and len(t) < 300 and not re.search(r"(the|and|with|from)\s", t):
            return "authors"
    if page_idx == 0 and par["bold"] and par["size"] >= 11 and par["bbox"][1] > 250:
        return "abstract" if is_clean_prose(t) else "formula_mixed"
    if len(t) < 32 and par["digits"] > 0.45 and not re.search(r"[。！？；]", t):
        return "table_cell"
    if par["formula"] > 0.45 and len(t) < 100 and not t.endswith("."):
        return "equation"
    if par["bold"] and len(t) < 80 and not t.endswith("."):
        return "heading"
    if not is_clean_prose(t):
        return "formula_mixed"
    return "body"


def mark_references(pages: list[list[dict]]) -> None:
    ref_pages = set()
    for i, paras in enumerate(pages):
        n = sum(1 for p in paras if p["kind"] == "reference")
        if n >= 3:
            ref_pages.add(i)
    changed = True
    while changed:
        changed = False
        for i in list(ref_pages):
            for j in (i - 1, i + 1):
                if 0 <= j < len(pages) and j not in ref_pages:
                    n = sum(1 for p in pages[j] if p["kind"] == "reference")
                    if n >= 2:
                        ref_pages.add(j)
                        changed = True
    for i in ref_pages:
        for p in pages[i]:
            if p["kind"] not in ("page_number",):
                p["kind"] = "reference"


KEEP = {"page_number", "reference", "meta", "authors", "equation", "table_cell",
        "formula_mixed"}


def build_plan(src: str, profile: str | None,
               params: dict | None = None) -> dict:
    set_params(params)
    doc = fitz.open(src)
    pages = []
    for pno, page in enumerate(doc):
        w, h = page.rect.width, page.rect.height
        lines = build_lines(page, w)
        columns = detect_columns(lines, w)
        paras = segment(lines, w, h, columns, w * 0.85)
        regions = equation_regions(page, w)
        pblocks = _page_blocks(page)
        colmin = min((p["bbox"][0] for p in paras), default=0)
        for p in paras:
            txt, bb = reading_text(pblocks, p)
            if len(txt) > max(20, len(p["text"]) * 0.5):
                p["text"] = txt
                p["bbox"] = [max(0.0, bb[0]), max(0.0, bb[1]),
                             min(w, bb[2]), min(h, bb[3])]
                p["formula"] = formula_ratio(txt)
                p["digits"] = digit_ratio(txt)
            p["region_equation"] = any(overlap_ratio(p["bbox"], r) > 0.20 for r in regions)
            p["kind"] = classify(p, pno, h, w * 0.85, colmin)
        # table page heuristic: many short numeric blocks
        if sum(1 for p in paras if p["kind"] == "table_cell") >= 5:
            for p in paras:
                if len(p["text"]) < 32 and p["digits"] > 0.35:
                    p["kind"] = "table_cell"
        pages.append(paras)
    mark_references(pages)

    entries = []
    for pno, paras in enumerate(pages):
        for p in paras:
            if not p["text"].strip():
                continue
            entries.append({
                "id": "p%d-%d" % (pno + 1, len(entries)),
                "page": pno,
                "bbox": [round(v, 1) for v in p["bbox"]],
                "size": round(p["size"], 2),
                "bold": bool(p["bold"]),
                "kind": p["kind"],
                "keep": p["kind"] in KEEP,
                "label": "",
                "en": p["text"],
                "zh": "",
            })
    stats = Counter(e["kind"] for e in entries)
    if profile is None:
        two = sum(1 for page in doc if detect_columns(build_lines(page, page.rect.width),
                                                      page.rect.width) == "two")
        profile = "two-col-latex" if two >= max(2, len(doc) // 4) else "single-col-word"
    return {"src": str(Path(src).resolve()), "pages": len(doc),
            "profile": profile, "entries": entries, "stats": dict(stats),
            "params": dict(P)}
