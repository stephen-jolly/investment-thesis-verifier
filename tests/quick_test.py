# -*- coding: utf-8 -*-
"""快速验证后端完整链路（UTF-8 安全）。"""
import json
import time
import httpx

THESIS = "贵州茅台2023年盈利增长主要来自主营业务"

def main():
    t0 = time.time()
    r = httpx.post(
        "http://127.0.0.1:8000/api/analyze",
        json={"thesis": THESIS},
        timeout=300.0,
    )
    elapsed = time.time() - t0
    body = r.json()
    print(f"HTTP {r.status_code}, elapsed {elapsed:.1f}s, ok={body.get('ok')}")
    if not body.get("ok"):
        print("ERROR:", body.get("error"))
        return
    res = body["result"]
    print("thscode:", res.get("thscode"))
    dec = res["decomposition"]
    print("target:", dec.get("target", {}).get("name"), dec.get("target", {}).get("thscode"))
    print("core_claim:", dec.get("core_claim"))
    print("clarifications:", dec.get("clarifications"))
    print("sub_questions:", len(dec.get("sub_questions", [])))
    for sq in dec.get("sub_questions", []):
        print("  -", sq.get("id"), sq.get("question"), "| tool:", sq.get("tool"))
    print("\n=== sub results ===")
    for sr in res.get("sub_results", []):
        meta = sr.get("tool_result_meta", {})
        an = sr.get("analysis", {})
        print(f"  {an.get('sub_question_id')}: fetch_ok={meta.get('ok')} verdict={an.get('summary_verdict')} evidences={len(an.get('evidences', []))}")
    con = res["conclusion"]
    print("\n=== conclusion ===")
    print("verdict:", con.get("verdict"), "| confidence:", con.get("confidence"))
    print("summary:", con.get("summary"))
    print("flip_conditions:", len(con.get("flip_conditions", [])))
    print("next_steps:", con.get("next_steps"))
    print("\ntrace:")
    for t in res["meta"]["trace"]:
        print(" ", t)
    with open("tests/quick_test_result.json", "w", encoding="utf-8") as f:
        json.dump(body, f, ensure_ascii=False, indent=2)
    print("\nSaved tests/quick_test_result.json")

if __name__ == "__main__":
    main()
