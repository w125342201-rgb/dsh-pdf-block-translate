# -*- coding: utf-8 -*-
"""Bounded self-evolution: replay stored cases and tune numeric profile params.

The model never writes code.  This script only adjusts the `params` block of
`profiles/<id>.json`, and only when a candidate beats the current parameters on
every stored case and keeps the display-equation count stable.  A copy of the
previous profile is kept under `profiles/_history/`.

Usage: python evolve.py [workdir] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import common  # noqa: E402


def coverage(plan: dict) -> tuple[float, int, int]:
    kinds = [e["kind"] for e in plan["entries"]]
    trans = sum(1 for e in plan["entries"] if not e["keep"])
    mixed = sum(1 for e in plan["entries"] if e["kind"] == "formula_mixed")
    denom = trans + mixed
    eq = sum(1 for k in kinds if k == "equation")
    return (trans / denom if denom else 0.0), eq, len(plan["entries"])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("workdir", nargs="?")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    cases_root = ROOT / "cases"
    profiles_dir = ROOT / "profiles"
    cases = []
    if cases_root.exists():
        for cj in sorted(cases_root.glob("*/case.json")):
            try:
                c = json.loads(cj.read_text(encoding="utf-8"))
            except Exception:
                continue
            src = cj.parent / "src.pdf"
            if src.exists():
                c["_src"] = str(src)
                cases.append(c)
    if not cases:
        print(json.dumps({"ok": True, "applied": False,
                          "reason": "没有可回放的 case（cases/*/src.pdf）",
                          "cases": 0}, ensure_ascii=False))
        return 0

    by_profile: dict[str, list[dict]] = {}
    for c in cases:
        by_profile.setdefault(c.get("profile", "single-col-word"), []).append(c)

    report = {"ok": True, "cases": len(cases), "profiles": {}}
    for pid, group in by_profile.items():
        pp = profiles_dir / (pid + ".json")
        try:
            prof = json.loads(pp.read_text(encoding="utf-8")) if pp.exists() else {}
        except Exception:
            prof = {}
        current = dict(common.DEFAULT_PARAMS)
        current.update(prof.get("params") or {})
        base_cov, base_eq = [], []
        for c in group:
            try:
                plan = common.build_plan(c["_src"], pid, current)
                cov, eq, _ = coverage(plan)
                base_cov.append(cov)
                base_eq.append(eq)
            except Exception as exc:
                report.setdefault("skipped", []).append("%s: %s" % (c.get("case_id"), exc))
        if not base_cov:
            continue
        base = sum(base_cov) / len(base_cov)
        base_eq_mean = sum(base_eq) / len(base_eq)
        r0 = float(current["clean_formula_ratio"])
        f0 = int(current["clean_frag_max"])
        ratios = sorted({round(max(0.10, min(0.24, r0 + d)), 3) for d in (-0.04, -0.02, 0, 0.02, 0.04)})
        frags = sorted({max(2, min(5, f0 + d)) for d in (-1, 0, 1)})
        best = None
        trials = 0
        for r in ratios:
            for f in frags:
                cand = dict(current)
                cand["clean_formula_ratio"] = r
                cand["clean_frag_max"] = f
                covs, eqs = [], []
                ok = True
                for c in group:
                    try:
                        plan = common.build_plan(c["_src"], pid, cand)
                        cov, eq, _ = coverage(plan)
                    except Exception:
                        ok = False
                        break
                    if eq < base_eq_mean * 0.9 or eq > base_eq_mean * 1.1:
                        ok = False
                        break
                    covs.append(cov)
                    eqs.append(eq)
                    trials += 1
                if not ok or not covs:
                    continue
                if min(covs) < base - 0.05:
                    continue
                score = sum(covs) / len(covs)
                if best is None or score > best[0]:
                    best = (score, r, f, covs)
        entry = {"baseline_coverage": round(base, 3),
                 "candidates_tried": trials,
                 "current": {"clean_formula_ratio": r0, "clean_frag_max": f0}}
        if best and base + 0.02 < best[0] <= base + 0.10:
            score, r, f, covs = best
            entry.update({"applied": True, "new_coverage": round(score, 3),
                          "params": {"clean_formula_ratio": r, "clean_frag_max": f}})
            if not args.dry_run:
                if pp.exists():
                    hist = profiles_dir / "_history"
                    hist.mkdir(exist_ok=True)
                    (hist / ("%s-%s.json" % (pid, time.strftime("%Y%m%d-%H%M%S")))).write_text(
                        pp.read_text(encoding="utf-8"), encoding="utf-8")
                prof.setdefault("params", {}).update({"clean_formula_ratio": r,
                                                      "clean_frag_max": f})
                prof.setdefault("history", []).append({
                    "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "cases": [c.get("case_id") for c in group],
                    "before": round(base, 3), "after": round(score, 3),
                    "clean_formula_ratio": r, "clean_frag_max": f})
                pp.write_text(json.dumps(prof, ensure_ascii=False, indent=2), encoding="utf-8")
        else:
            entry["applied"] = False
            entry["reason"] = "没有候选在覆盖率和公式稳定性上同时超过现状"
        if entry.get("applied") and len(group) < 2:
            entry["applied"] = False
            entry["reason"] = "只有 1 个 case；为避免单例过拟合，至少积累 2 篇再自动改参数"
        report["profiles"][pid] = entry

    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
