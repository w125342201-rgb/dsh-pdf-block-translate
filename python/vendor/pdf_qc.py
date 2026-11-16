#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一键验收：对译文 PDF 跑七项硬指标（2026-10-24 定稿，等价于手搓 verify 套件）。

用法: python pdf_qc.py <out.pdf> <src.pdf> [--colsplit 300]
退出码 0=全过 1=有缺陷。命中项逐条打印，可直接定位到页与行。

七项：
  1. 页数一致
  2. 逐页"实绘"图像 bbox 一致（get_image_info，剔除资源字典孤立图）
  3. 替换符 U+FFFD = 0
  4. 中文行缺字 = 0（msyh 缺字表：U+1D400 区数学斜体、⁻∇∼⊗∘≪≫∝ẑǁ、下标 ₀-₉）
  5. 行首标点 = 0（中文排版禁则）
  6. 中文压图(交叠>20%) = 0
  7. 同栏相邻中文行纵向重叠>1pt = 0；越栏中文行 = 0
"""
import argparse
import re
import sys

import fitz

MISS = (set("⁻ẑ∇ǁ⊗∘≪≫∝∼")
        | {chr(c) for c in range(0x1D400, 0x1D800)}
        | {chr(c) for c in range(0x2080, 0x208A)})
CLOSE = "。，、；：！？）》」』%…”"


def cjk(t):
    return re.search(r"[\u4e00-\u9fff]", t)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("src")
    ap.add_argument("--colsplit", type=float, default=300.0)
    args = ap.parse_args()

    doc, orig = fitz.open(args.out), fitz.open(args.src)
    fails = []

    # 1 页数
    if len(doc) != len(orig):
        fails.append(f"页数不一致 {len(doc)} vs {len(orig)}")

    # 2 实绘图像
    for i in range(min(len(doc), len(orig))):
        a = sorted(tuple(round(v, 1) for v in x["bbox"]) for x in orig[i].get_image_info())
        b = sorted(tuple(round(v, 1) for v in x["bbox"]) for x in doc[i].get_image_info())
        if a != b:
            fails.append(f"p{i+1} 图像不一致: 原{a} 译{b}")

    n_uff = sum(p.get_text().count("\ufffd") for p in doc)
    if n_uff:
        fails.append(f"替换符 U+FFFD x{n_uff}")

    n_miss = n_punct = n_overimg = n_coll = n_cross = 0
    # 原文各页"本来就跨栏"的行（标题/通栏图注）——译文在同样位置跨栏不算缺陷
    orig_cross = []
    # 原文各页行 bbox（叠行对跑用：原文本来就叠的不算缺陷，PRB 上下标排版常态）
    orig_lines = []
    for pg in orig:
        lines = []
        for blk in pg.get_text("dict")["blocks"]:
            if blk.get("type"):
                continue
            for ln in blk["lines"]:
                if "".join(s["text"] for s in ln["spans"]).strip():
                    lines.append(ln["bbox"])
        orig_lines.append(lines)
        # 同一视觉行常被 PyMuPDF 按字体/字距断成 2 段（Nature 系通栏图注典型），
        # 单段都不跨栏、拼起来才跨栏。先把「y 重叠 >60% 且水平间距 ≤6pt」的相邻段并成一行。
        merged = []
        for b in sorted(lines, key=lambda r: (round(r[1]), r[0])):
            for m in merged:
                oy = min(m[3], b[3]) - max(m[1], b[1])
                h = min(m[3] - m[1], b[3] - b[1])
                gap = b[0] - m[2]
                if oy > 0.6 * max(h, 1) and -4 <= gap <= 6:
                    m[0], m[1] = min(m[0], b[0]), min(m[1], b[1])
                    m[2], m[3] = max(m[2], b[2]), max(m[3], b[3])
                    break
            else:
                merged.append(list(b))
        orig_cross.append([b for b in merged if b[0] < args.colsplit < b[2] - 2])
    def orig_overlaps(pno, ra, rb):
        """原文同栏 y 相邻(±4pt)的两行是否也纵向重叠>1pt"""
        cands = [o for o in orig_lines[pno] if abs(o[1] - ra[1]) < 4 or abs(o[1] - rb[1]) < 4]
        for o in cands:
            for p in cands:
                if o is p:
                    continue
                ox = min(o[2], p[2]) - max(o[0], p[0])
                oy = min(o[3], p[3]) - max(o[1], p[1])
                if ox > 1 and oy > 1.0:
                    return True
        return False
    for i, pg in enumerate(doc):
        imgs = [x["bbox"] for x in pg.get_image_info()]
        rows = []
        for blk in pg.get_text("dict")["blocks"]:
            if blk.get("type"):
                continue
            for ln in blk["lines"]:
                t = "".join(s["text"] for s in ln["spans"])
                if not cjk(t):
                    continue
                # 4 缺字
                hits = set(t) & MISS
                if hits:
                    n_miss += 1
                    print(f"  [缺字] p{i+1} {sorted(hits)} {t[:30]}")
                # 5 行首标点
                tt = t.strip()
                if tt and tt[0] in CLOSE:
                    n_punct += 1
                    print(f"  [行首标点] p{i+1} {tt[:25]}")
                # 6 压图
                r = fitz.Rect(ln["bbox"])
                for ir in imgs:
                    inter = r & fitz.Rect(ir)
                    if not inter.is_empty and inter.get_area() / max(r.get_area(), 1) > 0.2:
                        n_overimg += 1
                        print(f"  [压图] p{i+1} {t[:25]}")
                rows.append(list(ln["bbox"]) + [t])
                # 7 越栏（原文同位置也跨栏的放行）
                x0, y0o, x1, y1o = ln["bbox"]
                if x0 < args.colsplit < x1 - 2 and (x1 - x0) < 500:
                    legit = any(
                        ob[0] <= x0 + 25 and ob[2] >= x1 - 60
                        and (min(ob[3], y1o) - max(ob[1], y0o) > 2
                             or abs((ob[1] + ob[3]) / 2 - (y0o + y1o) / 2) < 20)
                        for ob in orig_cross[i])
                    if not legit:
                        n_cross += 1
                        print(f"  [越栏] p{i+1} [{x0:.0f},{x1:.0f}] {t[:25]}")
        # 7 同栏叠行（按中心 x 分栏后栏内比较）
        rows.sort(key=lambda a: (0 if (a[0] + a[2]) / 2 <= args.colsplit else 1, a[1]))
        for a, b in zip(rows, rows[1:]):
            ca, cb = (a[0] + a[2]) / 2, (b[0] + b[2]) / 2
            if (ca <= args.colsplit) != (cb <= args.colsplit):
                continue
            ox = min(a[2], b[2]) - max(a[0], b[0])
            oy = min(a[3], b[3]) - max(a[1], b[1])
            if ox > 1 and oy > 1.0:
                # 与原文对跑：原文此处本来就叠（PRB 上下标/公式排版常态）则豁免
                if orig_overlaps(i, a, b):
                    continue
                n_coll += 1
                print(f"  [叠行] p{i+1} oy={oy:.1f} {a[4][:14]} | {b[4][:14]}")

    if n_miss:
        fails.append(f"缺字行 x{n_miss}")
    if n_punct:
        fails.append(f"行首标点 x{n_punct}")
    if n_overimg:
        fails.append(f"中文压图 x{n_overimg}")
    if n_coll:
        fails.append(f"同栏叠行 x{n_coll}")
    if n_cross:
        fails.append(f"越栏行 x{n_cross}")

    if fails:
        print("QC FAIL: " + "; ".join(fails))
        sys.exit(1)
    print(f"QC PASS 七项全过（{len(doc)} 页）")


if __name__ == "__main__":
    main()
