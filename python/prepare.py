# -*- coding: utf-8 -*-
"""Step 1: analyze the PDF and emit a translation template.

Usage: python prepare.py <src.pdf> [workdir] [--profile id]
Outputs (in workdir):
  blocks.json                 renderer input (all blocks)
  translations_template.json  the only file the agent edits
  plan.json                   immutable plan + hashes
  plan.md                     human summary
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("workdir", nargs="?")
    ap.add_argument("--profile", default=None)
    args = ap.parse_args()

    src = Path(args.src).resolve()
    if not src.exists():
        print(json.dumps({"ok": False, "errors": ["source not found: %s" % src]},
                         ensure_ascii=False))
        return 2
    workdir = Path(args.workdir).resolve() if args.workdir else src.with_name(
        src.stem + "_zh_work")
    workdir.mkdir(parents=True, exist_ok=True)

    profiles_dir = Path(__file__).resolve().parent.parent / "profiles"
    plan = common.build_plan(str(src), args.profile, None)
    pid = plan["profile"]
    pp = profiles_dir / (pid + ".json")
    if pp.exists():
        try:
            tuned = json.loads(pp.read_text(encoding="utf-8")).get("params") or {}
        except Exception:
            tuned = {}
        if tuned:
            plan = common.build_plan(str(src), pid, tuned)
    blocks = [{"id": e["id"], "page": e["page"], "bbox": e["bbox"],
               "size": e["size"], "bold": e["bold"], "text": e["en"]}
              for e in plan["entries"]]
    template = {"meta": {"src": str(src), "profile": plan["profile"],
                         "pages": plan["pages"],
                         "instructions": "Fill zh for every entry with keep=false. "
                                         "Use keep=true to leave a block in the original language. "
                                         "Optional bbox_override/label per entry."},
                "entries": [{k: e[k] for k in ("id", "page", "bbox", "size", "bold",
                                               "kind", "keep", "label", "en", "zh")}
                            for e in plan["entries"]]}

    (workdir / "blocks.json").write_text(json.dumps(blocks, ensure_ascii=False, indent=1),
                                         encoding="utf-8")
    (workdir / "translations_template.json").write_text(
        json.dumps(template, ensure_ascii=False, indent=1), encoding="utf-8")
    plan_out = {"src": str(src), "profile": plan["profile"], "pages": plan["pages"],
                "stats": plan["stats"], "params": plan.get("params", {}),
                "translatable": sum(1 for e in plan["entries"] if not e["keep"]),
                "kept": sum(1 for e in plan["entries"] if e["keep"]),
                "blocks_sha256": sha256(workdir / "blocks.json")}
    (workdir / "plan.json").write_text(json.dumps(plan_out, ensure_ascii=False, indent=1),
                                       encoding="utf-8")

    lines = ["# 翻译计划", "",
             "- 源文件：`%s`" % src,
             "- 版式：**%s**（%d 页）" % (plan["profile"], plan["pages"]),
             "- 可译块：%d，保留块：%d" % (plan_out["translatable"], plan_out["kept"]),
             "", "## 块类型统计", ""]
    for k, v in sorted(plan["stats"].items(), key=lambda kv: -kv[1]):
        lines.append("- %s: %d" % (k, v))
    lines += ["", "## 下一步", "",
              "编辑 `translations_template.json`，给每个 keep=false 的条目填 `zh`；",
              "然后调用 `pdf_finish`。校验不过不会生成 PDF。"]
    (workdir / "plan.md").write_text("\n".join(lines), encoding="utf-8")

    lessons_p = Path(__file__).resolve().parent.parent / "cases" / "lessons.md"
    lessons = lessons_p.read_text(encoding="utf-8").splitlines()[-8:] if lessons_p.exists() else []

    print(json.dumps({"ok": True, "workdir": str(workdir),
                      "profile": plan["profile"], "pages": plan["pages"],
                      "translatable": plan_out["translatable"],
                      "kept": plan_out["kept"], "stats": plan["stats"],
                      "lessons": lessons,
                      "template": str(workdir / "translations_template.json"),
                      "plan": str(workdir / "plan.md")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
