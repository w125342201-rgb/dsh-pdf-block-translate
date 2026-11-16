# -*- coding: utf-8 -*-
"""PDF 原位译文流水线（保留版面）。用法：
  python pdf_translate.py extract <src.pdf> blocks.json
  python pdf_translate.py render <src.pdf> blocks.json translations.json <out.pdf>
translations.json: { "<block_id>": "译文" 或 {"label": "Figure 1.", "text": "..."} }
未出现在 translations.json 的块：原文保留，不抹除。
"""
import sys, json, re, fitz

LEADING = 1.45
GAP_MAX = 32.0          # 行间距小于此值且 x 重叠 → 同段
CLOSE_PUNCT = "。，、；：！？）》」』%…”" + ",.:;!?)]}"
OPEN_PUNCT = "([{（【《「『"
SIMSUN = "C:/Windows/Fonts/msyh.ttc"   # 中文正文：微软雅黑（无衬线，视觉更大更醒目）
MSYHBD = "C:/Windows/Fonts/msyhbd.ttc"
CJK = re.compile(r'[\u4e00-\u9fff\u3000-\u303f]')

# 不可从中间断开的“西文/公式 token”字符集（含希腊字母与运算符）
GLUE = set("abcdefghijklmnopqrstuvwxyz"
           "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
           "0123456789"
           "αβγδεζηθικλμνξοπρστυφχψω"
           "ΑΒΓΔΕΖΗΘΙΚΛΜΝΞΟΠΡΣΤΥΦΧΨΩ"
           "μΔΣΦΨΩµ²³¹⁰·°"
           "+-/*=~^_<>|")

def tokenize(text):
    """把文本切成 token：西文/符号连续段为一个 token，其余逐字。"""
    toks, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        if c in GLUE:
            j = i
            while j < n and (text[j] in GLUE
                             or (text[j] == "." and j + 1 < n
                                 and (text[j + 1] in GLUE or text[j + 1].isdigit()))):
                j += 1
            toks.append(text[i:j])
            i = j
        else:
            toks.append(c)
            i += 1
    return toks

def line_bold_ratio(spans):
    b = sum(len(s["text"]) for s in spans
            if (s["flags"] & 16) or ("Bold" in s.get("font", "")))
    t = sum(len(s["text"]) for s in spans)
    return (b / t) if t else 0.0

def dom_size(spans):
    tot = {}
    for s in spans:
        n = len(s["text"])
        if n:
            tot[round(s["size"], 1)] = tot.get(round(s["size"], 1), 0) + n
    return max(tot, key=tot.get) if tot else 10.0

def collect_lines(page):
    out = []
    d = page.get_text("dict")
    for blk in d.get("blocks", []):
        if blk.get("type") != 0:
            continue
        for ln in blk.get("lines", []):
            spans = [s for s in ln.get("spans", []) if s["text"].strip()]
            if not spans:
                continue
            text = "".join(s["text"] for s in ln.get("spans", []))
            out.append({
                "bbox": ln["bbox"], "text": text, "spans": spans,
                "bold": line_bold_ratio(spans) > 0.6,
                "size": dom_size(spans),
            })
    out.sort(key=lambda r: (round(r["bbox"][1], 1), r["bbox"][0]))
    return out

def x_overlap(a, b, tol=8.0):
    return min(a[2], b[2]) - max(a[0], b[0]) > tol

def join_text(prev, nxt):
    if len(prev) <= 2 and prev.isalpha():
        return prev + nxt
    if prev.endswith("-"):
        return prev[:-1] + nxt
    if CJK.search(prev[-1:]) or CJK.search(nxt[:1]):
        return prev + nxt
    return prev + " " + nxt

def group_paragraphs(lines, page_w):
    mid = page_w / 2
    streams = {"full": [], "left": [], "right": []}
    for ln in lines:
        x0, x1 = ln["bbox"][0], ln["bbox"][2]
        if x1 - x0 > 0.55 * page_w:
            streams["full"].append(ln)
        elif x1 <= mid + 12:
            streams["left"].append(ln)
        elif x0 >= mid - 12:
            streams["right"].append(ln)
        else:
            streams["full"].append(ln)
    paras = []
    for name, stream in streams.items():
        stream.sort(key=lambda r: (round(r["bbox"][1], 1), r["bbox"][0]))
        col_paras = []
        prev = None
        for ln in stream:
            if prev is not None:
                dy = ln["bbox"][1] - prev["bbox"][1]
                dx = abs(ln["bbox"][0] - prev["bbox"][0])
                p = col_paras[-1]
                if -2 < dy < 32 and dx < 45:
                    p["lines"].append(ln)
                    p["text"] = join_text(p["text"], ln["text"])
                    p["bbox"] = [min(p["bbox"][0], ln["bbox"][0]),
                                 min(p["bbox"][1], ln["bbox"][1]),
                                 max(p["bbox"][2], ln["bbox"][2]),
                                 max(p["bbox"][3], ln["bbox"][3])]
                    prev = ln
                    continue
            col_paras.append({"lines": [ln], "text": ln["text"],
                              "bbox": list(ln["bbox"]), "size": ln["size"],
                              "bold": ln["bold"]})
            prev = ln
        paras.extend(col_paras)
    for p in paras:
        b = sum(len(l["text"]) for l in p["lines"] if l["bold"])
        t = sum(len(l["text"]) for l in p["lines"])
        p["bold"] = t > 0 and b / t > 0.6
    paras.sort(key=lambda p: (p["bbox"][1], p["bbox"][0]))
    return paras

def extract(src, out_json):
    doc = fitz.open(src)
    blocks = []
    for pno, page in enumerate(doc):
        W, H = page.rect.width, page.rect.height
        for p in group_paragraphs(collect_lines(page), W):
            x0, y0, x1, y1 = p["bbox"]
            text = p["text"].strip()
            if not text:
                continue
            if "Downloaded from" in text:
                continue
            # 页码 / 页脚条：页面底部窄条
            if y0 > H - 45 and len(text) < 60:
                continue
            # 竖排水印条
            if x1 - x0 < 15 and y1 - y0 > 80:
                continue
            tot = sum(len(l["text"]) for l in p["lines"])
            wsize = (sum(l["size"] * len(l["text"]) for l in p["lines"]) / tot
                     if tot else p["size"])
            blocks.append({
                "id": f"p{pno+1}-{len(blocks)}",
                "page": pno, "bbox": [round(v, 1) for v in p["bbox"]],
                "size": round(wsize, 2), "bold": p["bold"], "text": text,
            })
    json.dump(blocks, open(out_json, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print(f"blocks: {len(blocks)} -> {out_json}")

def wrap_cjk(text, font, size, maxw, first_maxw=None):
    """first_maxw：仅第 1 行使用的宽度上限（图注 label 前缀占位时用）。
    render 里第 2 行起是从 x0 起绘的，所以只有第 1 行受 label 缩进影响；
    若对所有行都套用收窄宽度，图注会集体右侧留一大块参差空白、并被无谓地压小字号。"""
    if not text:
        return [""], size
    if size <= 4.0:
        lines = [text[i:i+60] for i in range(0, len(text), 60)]
        return lines, size
    lines, cur = [], ""

    def cw():
        return first_maxw if (first_maxw is not None and not lines) else maxw

    _toks = tokenize(text)
    for idx, tk in enumerate(_toks):
        nxt = _toks[idx + 1] if idx + 1 < len(_toks) else ""
        if not cur:
            if tk.strip() == "":
                continue
            # 行首禁则：上一行刚被收尾标点刷出后，紧随的收尾标点不得另起一行
            # （否则出现「行首 。」），并入上一行末（标点悬挂）
            if lines and tk[0] in CLOSE_PUNCT:
                lines[-1] += tk
                continue
            cur = tk
        elif (tk[0] in OPEN_PUNCT       # 左括号不留在行末（向后看一个 token）
              and font.text_length(tk + nxt, size) <= cw()
              and font.text_length(cur + tk + nxt, size) > cw()):
            lines.append(cur)
            cur = tk
        elif font.text_length(cur + tk, size) <= cw():
            cur += tk
        elif tk[0] in CLOSE_PUNCT:      # 行首收尾标点 → 挂上一行末
            cur += tk
            lines.append(cur)
            cur = ""
        else:
            lines.append(cur)
            cur = tk
        # 单个 token 本身超宽 → 硬断
        while len(cur) > 1 and font.text_length(cur, size) > cw():
            k = len(cur) - 1
            while k > 1 and font.text_length(cur[:k], size) > cw():
                k -= 1
            lines.append(cur[:k])
            cur = cur[k:]
    if cur:
        lines.append(cur)
    if len(lines) > 1 and len(lines[-1]) <= 3 and lines[-1].strip(CLOSE_PUNCT + " \t") == "":
        return wrap_cjk(text, font, size - 0.25, maxw, first_maxw)
    if len(lines) > 1 and len(lines[-1]) <= 3:
        return wrap_cjk(text, font, size - 0.25, maxw, first_maxw)
    return lines, size

def max_bottom(box, obstacles, page_h):
    """避障向下扩：找 box 正下方最近的障碍物（其他块/图），返回可扩到的 y1。
    移植自 BabelDOC typesetting.get_max_bottom_space（2026-10-25）。"""
    lim = page_h - 20
    for o in obstacles:
        if o.y0 >= box.y1 - 2:
            xov = min(o.x1, box.x1) - max(o.x0, box.x0)
            if xov > 8:
                lim = min(lim, o.y0 - (0.5 if box.height < 36 else 3))
    return lim


def max_right(box, obstacles, page_w):
    """避障向右扩：找 box 右侧 y 有重叠的最近障碍物，上限页宽 90%。
    栏感知是天然的：右栏的块本身就是障碍物，扩到栏缝自动停。
    移植自 BabelDOC typesetting.get_max_right_space（2026-10-25）。"""
    lim = page_w * 0.9
    for o in obstacles:
        if o.x0 >= box.x1 - 2:
            yov = min(o.y1, box.y1) - max(o.y0, box.y0)
            if yov > 4:
                lim = min(lim, o.x0 - 3)
    return lim


def render(src, blocks_json, trans_json, out_pdf):
    blocks = json.load(open(blocks_json, encoding="utf-8"))
    trans = json.load(open(trans_json, encoding="utf-8"))
    todo = [b for b in blocks if b["id"] in trans]
    doc = fitz.open(src)
    pages = {}
    for b in todo:
        pg = doc[b["page"]]
        r = fitz.Rect(b["bbox"])
        e = trans[b["id"]]
        if isinstance(e, dict) and "bbox" in e:
            # 抹除范围取“原块 bbox ∪ 覆盖 bbox”，否则被合并吞掉的碎片会残留
            r = r | fitz.Rect(e["bbox"])
        pg.add_redact_annot(r)
        pages.setdefault(b["page"], pg)
    for pno, pg in pages.items():
        pg.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE,
                            graphics=fitz.PDF_REDACT_LINE_ART_NONE)
    f_reg = fitz.Font(fontfile=SIMSUN)
    f_bold = fitz.Font(fontfile=MSYHBD)
    # 障碍物图（BabelDOC 策略）：所有块的 bbox + 实绘图像，供避障扩框用。
    # 被翻译块本身也要当障碍（它们会被重绘，原 bbox 就是其占位）；自块因
    # y0/y1 关系天然不触发。碎片若被 {bbox} 覆盖框吞并，其 bbox 落在框内，
    # 同样不构成下方障碍。
    obst = {}
    for b in blocks:
        obst.setdefault(b["page"], []).append(fitz.Rect(b["bbox"]))
    for i, pg in enumerate(doc):
        for info in pg.get_image_info():
            obst.setdefault(i, []).append(fitz.Rect(info["bbox"]))
    writers = {}
    for b in todo:
        pg = doc[b["page"]]
        tw = writers.setdefault(b["page"], fitz.TextWriter(pg.rect))
        x0, y0, x1, y1 = b["bbox"]
        entry = trans[b["id"]]
        label = ""
        if isinstance(entry, dict):
            label, text = entry.get("label", ""), entry["text"]
            if "bbox" in entry:
                x0, y0, x1, y1 = entry["bbox"]
        else:
            text = entry
        leadK = 1.0
        if isinstance(entry, dict) and entry.get("leading"):
            leadK = float(entry["leading"])      # 单栏长文可按组放宽行距，避免页底大片空白
        maxw = x1 - x0
        hgt = y1 - y0
        m = re.match(r"^((?:Figure|Scheme|Table|Fig\.)\s*\S{1,4}[.:]?)\s*", text)
        if m and not label:
            label, text = m.group(1), text[m.end():]
        size = b["size"]
        lab_w = f_bold.text_length(label, size) if label else 0
        # 前缀过长会把首行可用宽度挤到 0 甚至负数 ⇒ 并入正文，避免画到页面外
        if label and maxw - (lab_w + 5) <= 20:
            text = label + " " + text
            label, lab_w = "", 0
        lines = [text]
        orig_size = size
        exp_down = exp_right = False
        for _ in range(120):
            f = f_bold if (b["bold"] and not label) else f_reg
            body_w = maxw - (lab_w + 5) if label else maxw
            if maxw > 20:
                # wrap_cjk 在「末行只剩 ≤3 字」时会降 0.25pt 重排，并连实际用的字号一起返回。
                # 必须采纳该字号：否则按原字号绘制会让每行横向超出 maxw 约 2.4%/级，
                # 表现为译文越过栏界、侵入相邻栏（栏间距窄的期刊尤其明显）。
                # first_maxw：只有首行受 label 缩进影响，第 2 行起按整框宽排。
                lines, size = wrap_cjk(text, f, size, maxw, body_w if label else None)
            else:
                lines = [text]
            need = len(lines) * size * LEADING * leadK
            # 末尾基线的降部只允许溢出 0.25 字号；否则会压到下方相邻块（同一区域两块常首尾相接）
            if need <= hgt + size * 0.25 or size <= 4.5:
                break
            if size > 0.8 * orig_size:
                size -= 0.25
                continue
            # 缩到 80% 仍放不下 → BabelDOC 策略：先避障向下扩框，字号复原重试
            if not exp_down:
                exp_down = True
                ny1 = max_bottom(fitz.Rect(x0, y0, x1, y1),
                                 obst.get(b["page"], []), pg.rect.height)
                if ny1 - y1 > 0.5 * orig_size:
                    y1 = ny1
                    hgt = y1 - y0
                    size = orig_size
                    print(f"[AUTOFIT] {b['id']}: 向下扩框至 y{ny1:.0f}（避障）")
                    continue
            # 向下没得扩 → 缩到 70% 再试避障向右扩
            if not exp_right and size <= 0.7 * orig_size:
                exp_right = True
                nx1 = max_right(fitz.Rect(x0, y0, x1, y1),
                                obst.get(b["page"], []), pg.rect.width)
                if nx1 - x1 > 4:
                    x1 = nx1
                    maxw = x1 - x0
                    size = orig_size
                    print(f"[AUTOFIT] {b['id']}: 向右扩框至 x{nx1:.0f}（避障，栏缝自动停）")
                    continue
            size -= 0.25
        if size < 6.5:
            print(f"[QC] {b['id']}: 字号被压到 {size:.2f}pt（框 {hgt:.0f}pt 高 / 译文 {len(text)} 字）——检查内容是否分错块")
        if not text.strip() and hgt > 60:
            print(f"[QC] {b['id']}: 空译文但框高 {hgt:.0f}pt——确认是否真的该留空")
        f = f_bold if (b["bold"] and not label) else f_reg
        base = y0 + size
        first = True
        if label:
            tw.append(fitz.Point(x0, base), label, font=f_bold, fontsize=size)
            for ln in lines:
                if ln:
                    px = x0 + lab_w + 5 if first else x0
                    tw.append(fitz.Point(px, base), ln, font=f_reg, fontsize=size)
                base += size * LEADING * leadK
                first = False
        else:
            for ln in lines:
                if ln:
                    tw.append(fitz.Point(x0, base), ln, font=f, fontsize=size)
                base += size * LEADING * leadK
    for pno, tw in writers.items():
        tw.write_text(doc[pno])
    try:
        doc.subset_fonts()
    except Exception as e:
        print("subset_fonts skipped:", e)
    doc.save(out_pdf, garbage=4, deflate=True)
    print(f"translated blocks: {len(todo)} -> {out_pdf}")

if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "extract":
        extract(sys.argv[2], sys.argv[3])
    elif cmd == "render":
        render(sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5])
