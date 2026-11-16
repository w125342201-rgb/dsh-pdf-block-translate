# -*- coding: utf-8 -*-
"""Step 2: validate the filled template and render the Chinese PDF.

Usage: python finish.py <workdir> [--out out.pdf] [--allow-partial]
Exit 0 only when validation and QC both pass; otherwise exit 2 and print a
machine-readable error list.  No PDF is produced on failure.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

import fitz

HERE = Path(__file__).resolve().parent
VENDOR = HERE / "vendor"
sys.path.insert(0, str(VENDOR))
import pdf_translate  # noqa: E402

PUA = re.compile(r"[\ue000-\uf8ff]")
BAD = set("⁻ẑ∇ǁ⊗∘≪≫∝∼⋅")


def fail(errors: list[str], warnings: list[str] | None = None) -> int:
    print(json.dumps({"ok": False, "errors": errors,
                      "warnings": warnings or []}, ensure_ascii=False))
    return 2


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("workdir")
    ap.add_argument("--out", default=None)
    ap.add_argument("--allow-partial", action="store_true")
    args = ap.parse_args()

    wd = Path(args.workdir).resolve()
    plan = json.loads((wd / "plan.json").read_text(encoding="utf-8"))
    tpl = json.loads((wd / "translations_template.json").read_text(encoding="utf-8"))
    src = Path(plan["src"])
    out = Path(args.out).resolve() if args.out else src.with_name(
        src.stem + "_中文全文翻译.pdf")
    errors: list[str] = []
    warnings: list[str] = []

    doc = fitz.open(str(src))
    sizes = [(p.rect.width, p.rect.height) for p in doc]
    entries = tpl["entries"]
    eq_boxes = [(e["page"], e["bbox"]) for e in entries if e["kind"] == "equation"]

    def overlap(a, b):
        ix = min(a[2], b[2]) - max(a[0], b[0])
        iy = min(a[3], b[3]) - max(a[1], b[1])
        return ix * iy if ix > 0 and iy > 0 else 0.0

    trans = {}
    for e in entries:
        eid, zh = e["id"], (e.get("zh") or "").strip()
        keep = bool(e.get("keep"))
        if keep:
            if zh:
                warnings.append("%s: keep=true，zh 被忽略" % eid)
            continue
        if not zh:
            if args.allow_partial:
                warnings.append("%s: 未填 zh（allow_partial）" % eid)
                continue
            errors.append("%s: 缺少 zh（keep=false）" % eid)
            continue
        if len(zh) < 2:
            errors.append("%s: zh 过短" % eid)
            continue
        if e["size"] < 9.0 and e["kind"] != "caption":
            errors.append("%s: 代表块字号 %.1fpt < 9pt（可能选到了公式碎片）" % (eid, e["size"]))
        w, h = sizes[e["page"]]
        bb = e.get("bbox_override") or e["bbox"]
        if not (0 <= bb[0] < bb[2] <= w + 1 and 0 <= bb[1] < bb[3] <= h + 1):
            errors.append("%s: bbox 越出页面" % eid)
        for pno, eb in eq_boxes:
            if pno == e["page"] and overlap(bb, eb) > 0.15 * max(
                    (bb[2] - bb[0]) * (bb[3] - bb[1]), 1):
                errors.append("%s: bbox 覆盖了公式块" % eid)
                break
        if PUA.search(zh):
            errors.append("%s: zh 含私用区字符" % eid)
        if "\ufffd" in zh:
            errors.append("%s: zh 含替换符" % eid)
        bad = sorted(set(zh) & BAD)
        if bad:
            errors.append("%s: zh 含缺字 %s" % (eid, bad))
        en_len = max(len(e["en"]), 1)
        ratio = len(zh) / en_len
        if ratio < 0.08 or ratio > 0.90:
            errors.append("%s: 中英长度比 %.2f 异常" % (eid, ratio))
        label = (e.get("label") or "").strip()
        if len(label) > 16:
            errors.append("%s: label 过长" % eid)
        trans[eid] = {"text": zh, "bbox": [round(v, 1) for v in bb]}
        if label:
            trans[eid]["label"] = label

    if errors:
        return fail(errors, warnings)
    if not trans and not args.allow_partial:
        return fail(["没有任何可渲染条目"], warnings)

    t0 = time.time()
    (wd / "translations.json").write_text(json.dumps(trans, ensure_ascii=False, indent=1),
                                          encoding="utf-8")
    step1 = wd / "step1.pdf"
    pdf_translate.render(str(src), str(wd / "blocks.json"),
                         str(wd / "translations.json"), str(step1))

    w = sizes[0][0]
    colsplit = w + 5 if plan["profile"] == "single-col-word" else w * 0.5 + 8
    qc1 = subprocess.run([sys.executable, str(VENDOR / "pdf_qc.py"),
                          str(step1), str(src), "--colsplit", str(colsplit)],
                         capture_output=True, text=True, encoding="utf-8")
    if qc1.returncode != 0:
        return fail(["QC 未通过（step1）", qc1.stdout.strip()[-1500:]], warnings)

    step2 = wd / "step2.pdf"
    cl = subprocess.run([sys.executable, str(VENDOR / "clean_orphan_lineart.py"),
                         str(step1), str(wd / "blocks.json"),
                         str(wd / "translations.json"), str(step2), "--apply"],
                        capture_output=True, text=True, encoding="utf-8")
    if cl.returncode != 0:
        return fail(["清理孤儿线失败", (cl.stdout + cl.stderr).strip()[-800:]], warnings)
    if not step2.exists():
        step2 = step1  # no orphan lines -> the cleaner returns without writing

    # remove vector remnants (radicals, fraction rules) fully inside erased rects
    d = fitz.open(str(step2))
    rects_by_page: dict[int, list[fitz.Rect]] = {}
    for e in entries:
        if e["id"] in trans:
            rects_by_page.setdefault(e["page"], []).append(fitz.Rect(trans[e["id"]]["bbox"]))
    for pno, rects in rects_by_page.items():
        pg = d[pno]
        img_rects = [fitz.Rect(i["bbox"]) for i in pg.get_image_info()]
        for dr in pg.get_drawings():
            r = fitz.Rect(dr["rect"])
            if r.width > 400 or r.height > 400:
                continue
            if any((r & ir).get_area() > 0.1 * max(r.get_area(), 1) for ir in img_rects):
                continue
            if any(r in q for q in rects):
                pg.add_redact_annot(r + (-0.5, -0.5, 0.5, 0.5), fill=False)
        pg.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE,
                            graphics=fitz.PDF_REDACT_LINE_ART_REMOVE_IF_COVERED,
                            text=fitz.PDF_REDACT_TEXT_NONE)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp_out = wd / "final_check.pdf"
    d.save(str(tmp_out), garbage=4, deflate=True)
    d.close()

    qc2 = subprocess.run([sys.executable, str(VENDOR / "pdf_qc.py"),
                          str(tmp_out), str(src), "--colsplit", str(colsplit)],
                         capture_output=True, text=True, encoding="utf-8")
    if qc2.returncode != 0:
        try:
            tmp_out.unlink()
        except OSError:
            pass
        return fail(["QC 未通过（final）", qc2.stdout.strip()[-1500:]], warnings)
    os.replace(tmp_out, out)

    cjk = sum(len(re.findall(r"[\u4e00-\u9fff]", p.get_text()))
              for p in fitz.open(str(out)))
    record = {"ok": True, "out": str(out), "profile": plan["profile"],
              "pages": plan["pages"], "translated": len(trans),
              "kept": sum(1 for e in entries if e.get("keep")),
              "cjk": cjk, "seconds": round(time.time() - t0, 1),
              "qc": "PASS", "warnings": warnings}
    (wd / "run_record.json").write_text(json.dumps(record, ensure_ascii=False, indent=1),
                                        encoding="utf-8")

    # ---- case store: every successful run becomes a replayable case ----
    try:
        case_root = HERE.parent / "cases"
        case_id = hashlib.sha1(src.read_bytes()).hexdigest()[:12]
        cdir = case_root / case_id
        cdir.mkdir(parents=True, exist_ok=True)
        if not (cdir / "src.pdf").exists():
            shutil.copy2(src, cdir / "src.pdf")
        kinds = Counter(e["kind"] for e in entries)
        case = {"case_id": case_id, "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                "file": src.name, "profile": plan["profile"],
                "params": plan.get("params", {}), "pages": plan["pages"],
                "entries": len(entries), "kept": record["kept"],
                "translated": len(trans), "cjk": cjk,
                "formula_mixed_kept": sum(1 for e in entries
                                          if e["kind"] == "formula_mixed" and e.get("keep")),
                "kinds": dict(kinds), "qc": "PASS", "seconds": record["seconds"]}
        (cdir / "case.json").write_text(json.dumps(case, ensure_ascii=False, indent=1),
                                        encoding="utf-8")
        with open(case_root / "index.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(case, ensure_ascii=False) + "\n")
        lesson = "- %s | %s | %s | 译%d/留%d | formula_mixed保留%d | QC PASS" % (
            case["ts"], case["file"], case["profile"], len(trans),
            record["kept"], case["formula_mixed_kept"])
        lessons_p = case_root / "lessons.md"
        old = lessons_p.read_text(encoding="utf-8").splitlines() if lessons_p.exists() else []
        old = (old + [lesson])[-200:]
        lessons_p.write_text("\n".join(old) + "\n", encoding="utf-8")
    except Exception as exc:  # case recording must never block delivery
        warnings.append("case recording skipped: %s" % exc)
        record["warnings"] = warnings

    print(json.dumps(record, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
