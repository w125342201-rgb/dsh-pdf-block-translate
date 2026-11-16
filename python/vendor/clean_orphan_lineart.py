# -*- coding: utf-8 -*-
"""清理“矢量公式残留碎片”。

现象：原理文里用矢量线画出的减号 / 分数线 / 根号横线，在所在段落被译文整体替换后，
文字层被抹掉、这些细线却留在原处，表现为译文里突然多出一道短线（常划穿某个西文 token）。

判据（只认“短横线”形状，避免误删图注框的边框线、表格线）：
  - 高度 <= 1.8pt、宽度 2~120pt
  - 要么「暗色实心填充」，要么「只有描边：描边色暗、线宽 <= 2pt、整条都是水平线段」
    （2026-10-08 补：根号上划线常是无填充的描边线，且可长达 45pt，原 2~28pt + 必须有 fill
     的判据会整条漏掉）
  - 完全落在某个“已翻译块”的抹除范围内

用法：
  python clean_orphan_lineart.py <src.pdf> blocks.json translations.json <out.pdf>          # 只扫描
  python clean_orphan_lineart.py <src.pdf> blocks.json translations.json <out.pdf> --apply  # 扫描并清除
清除用 redaction 完成（text=NONE / images=NONE / graphics=REMOVE_IF_COVERED），
所以文字与图片都不受影响，只删掉被小块完整覆盖的那几根线。
"""
import json
import os
import sys

import fitz

MAX_H = 1.8
# MAX_W 原本是 120：结果**节标题下划线**（常见 140–320pt 宽，Word 导出型论文尤其常见）
# 整类被漏掉，中文化后残留成一条横穿中文字形下缘的黑线。放宽到 600 覆盖整栏宽度；
# 误删风险仍由「线必须完整落在某个已翻译块矩形内」这条判据兜住。
MIN_W, MAX_W = 2.0, 600.0


def is_orphan_line(dr):
    """暗色实心填充的细横线，或「只有描边」的细横线（根号上划线常属后者）。"""
    fill, col = dr.get("fill"), dr.get("color")
    if fill is not None:
        return sum(fill) <= 1.6                      # 原判据：暗色实心
    if col is None or sum(col) > 1.6 or (dr.get("width") or 0.0) > 2.0:
        return False
    items = dr.get("items", [])                      # 描边线：必须整条都是水平线段
    if not items:
        return False
    for it in items:
        if it[0] != "l":
            return False
        p1, p2 = it[1], it[2]
        if abs(p1.y - p2.y) > 1.0:
            return False
    return True


def redact_rects(blocks_json, trans_json):
    blocks = json.load(open(blocks_json, encoding="utf-8"))
    trans = json.load(open(trans_json, encoding="utf-8"))
    out = {}
    for b in blocks:
        if b["id"] not in trans:
            continue
        r = fitz.Rect(b["bbox"])
        e = trans[b["id"]]
        if isinstance(e, dict) and "bbox" in e:
            r = r | fitz.Rect(e["bbox"])
        out.setdefault(b["page"], []).append(r)
    return out


def find_orphans(doc, rr):
    """返回 {page_index: [Rect, ...]}"""
    hits = {}
    for pno, pg in enumerate(doc):
        for dr in pg.get_drawings():
            r = fitz.Rect(dr["rect"])
            if r.height > MAX_H or not (MIN_W <= r.width <= MAX_W):
                continue
            if not is_orphan_line(dr):
                continue
            for q in rr.get(pno, []):
                if r in q:
                    hits.setdefault(pno, []).append(r)
                    break
    return hits


def cluster(rects, gap=8.0, ytol=3.0):
    """把同页上彼此邻近的孤儿线并成一只「大 annot」。

    【为什么必须聚簇】一条 Word 段落下边框常被 PDF 拆成 5 段
    （72→165.3 / 165.3→168.9 / 168.9→199.6 / 199.6→203.7 / 203.7→418.6）。
    `LINE_ART_REMOVE_IF_COVERED` 要求图形**完整**落在 annot 内，而每次
    `apply_redactions` 重写内容流后，相邻的填充矩形会**重新合并成一条更长路径**，
    其 bbox 随即超出原来那几个小 annot ⇒ 单轮只能删掉一部分（实测 26→5→3→2 收敛极慢）。
    先把相邻段并成一只覆盖整条线的 annot，一轮即可清干净。"""
    out = [fitz.Rect(r) for r in rects]
    changed = True
    while changed:
        changed = False
        for i in range(len(out)):
            for j in range(i + 1, len(out)):
                a, b = out[i], out[j]
                if (a.y0 <= b.y1 + ytol and b.y0 <= a.y1 + ytol
                        and a.x0 <= b.x1 + gap and b.x0 <= a.x1 + gap):
                    out[i] = a | b
                    del out[j]
                    changed = True
                    break
            if changed:
                break
    return out


def main(src, blocks_json, trans_json, out_pdf, apply=False):
    rr = redact_rects(blocks_json, trans_json)
    doc = fitz.open(src)
    hits = find_orphans(doc, rr)
    n = 0
    for pno in sorted(hits):
        for r in hits[pno]:
            print("  p%d %s" % (pno + 1, [round(v, 1) for v in r]))
            n += 1
    print("orphan line art: %d" % n)
    if not apply or not n:
        return
    # padding 1.0pt：get_drawings() 的 rect 与清除用的 rect 常有 0.4–0.6pt 的 y 偏移
    # （同一条线被 PDF 拆成多段）。配合 cluster() 聚簇，多跑几轮兜底。
    for _ in range(8):
        hits = find_orphans(doc, rr)
        if not hits:
            break
        for pno, rects in hits.items():
            pg = doc[pno]
            for r in cluster(rects):
                pg.add_redact_annot(r + (-1.0, -1.0, 1.0, 1.0), fill=False)
        for pno in hits:
            doc[pno].apply_redactions(
                images=fitz.PDF_REDACT_IMAGE_NONE,
                graphics=fitz.PDF_REDACT_LINE_ART_REMOVE_IF_COVERED,
                text=fitz.PDF_REDACT_TEXT_NONE,
            )
    tmp = out_pdf + ".tmp.pdf"
    doc.save(tmp, garbage=4, deflate=True)
    doc.close()
    os.replace(tmp, out_pdf)
    print("cleaned -> %s" % out_pdf)

    # 复核
    chk = fitz.open(out_pdf)
    left = find_orphans(chk, rr)
    print("remaining orphans: %d" % sum(len(v) for v in left.values()))


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--apply"]
    main(*args, apply="--apply" in sys.argv)
