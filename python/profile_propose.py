# -*- coding: utf-8 -*-
"""Optional: read run_record.json and suggest profile adjustments."""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path


def main() -> int:
    wd = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path.cwd()
    rec_p = wd / "run_record.json"
    tpl_p = wd / "translations_template.json"
    if not rec_p.exists():
        print(json.dumps({"ok": False, "errors": ["run_record.json not found; run pdf_finish first"]},
                         ensure_ascii=False))
        return 2
    rec = json.loads(rec_p.read_text(encoding="utf-8"))
    tpl = json.loads(tpl_p.read_text(encoding="utf-8")) if tpl_p.exists() else {"entries": []}
    entries = tpl.get("entries", [])
    kinds = Counter(e.get("kind") for e in entries)
    kept = [e for e in entries if e.get("keep")]
    todo = [e for e in entries if not e.get("keep")]
    done = [e for e in todo if (e.get("zh") or "").strip()]
    suggestions = []
    if todo:
        formula_mixed = sum(1 for e in kept if e.get("kind") == "formula_mixed")
        if formula_mixed and formula_mixed >= max(5, len(entries) // 10):
            suggestions.append(
                "有 %d 个 formula_mixed 块按 keep 保留；若译文覆盖不足，"
                "可对其中公式简单的块 override keep=false 并给出忠实译文。" % formula_mixed)
        if len(done) < len(todo):
            suggestions.append("还有 %d 个可译块未填 zh。" % (len(todo) - len(done)))
    if rec.get("cjk", 0) < 300:
        suggestions.append("译文 CJK 字符过少（%d），检查是否只译了标题/图注。" % rec.get("cjk", 0))
    if rec.get("qc") != "PASS":
        suggestions.append("QC 未通过，先修版面问题再调 profile。")
    if not suggestions:
        suggestions.append("未发现需要调整的项；可保持当前 profile。")
    print(json.dumps({
        "ok": True, "workdir": str(wd), "profile": rec.get("profile"),
        "kinds": dict(kinds), "entries": len(entries), "kept": len(kept),
        "translatable": len(todo), "translated": len(done),
        "suggestions": suggestions,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
