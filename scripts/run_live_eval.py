"""Ask the demo questions against the running API, as the right demo users, and check each answer
against sample_data/expected/demo_questions.json: outcome, key facts, required sources, and strings
that must never appear (for example another tenant's numbers).

Uses the real LLM: about 20 questions, a few cents at most. Answers are cached, so re-runs are free
until new data or feedback changes the versions.

Usage:
    uv run python scripts/run_live_eval.py
    uv run python scripts/run_live_eval.py --only q01,q09
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import httpx

EXPECTED = Path(__file__).resolve().parents[1] / "sample_data" / "expected" / "demo_questions.json"


def _numbers(value: float) -> set[str]:
    return {f"{value:.2f}", f"{value:.1f}", f"{value:g}"}


def _has_number(text: str, value: float) -> bool:
    return any(re.search(rf"(?<![\d.]){re.escape(n)}(?![\d])", text) for n in _numbers(value))


def fact_problems(text: str, facts: dict[str, Any]) -> list[str]:
    """Tolerant checks that the answer states the key facts; every applicable check must pass."""
    lowered = text.lower()
    problems: list[str] = []
    for key in ("attendance_pct", "overall_attendance_pct"):
        if isinstance(facts.get(key), int | float) and not _has_number(text, float(facts[key])):
            problems.append(f"expected {facts.get('display', facts[key])}")
    for key in ("employee_name", "department"):
        if isinstance(facts.get(key), str) and facts[key].lower() not in lowered:
            problems.append(f"expected {facts[key]}")
    people = facts.get("present")
    if isinstance(people, list) and people:
        names = [str(p["employee_name"]) for p in people]
        found = sum(1 for name in names if name.lower() in lowered)
        if found < 0.8 * len(names):
            problems.append(f"only {found}/{len(names)} present employees named")
    if facts.get("remarks_visible") and isinstance(facts.get("remarks"), str):
        words = {w for w in re.findall(r"[a-z]{5,}", facts["remarks"].lower())}
        if sum(1 for w in words if w in lowered) < len(words) / 2:
            problems.append(f"remark not reported: {facts['remarks']!r}")
    for key in ("check_in", "check_out"):
        if isinstance(facts.get(key), str) and facts[key] not in text:
            problems.append(f"expected {key} {facts[key]}")
    late = facts.get("late_arrivals_excluding_needs_review")
    if isinstance(late, int) and not re.search(rf"\b{late}\b", text):
        problems.append(f"expected {late} late arrivals (plus pending review disclosed)")
    return problems


def evaluate(item: dict[str, Any], response: dict[str, Any]) -> list[str]:
    expected = item.get("expected", {})
    problems: list[str] = []
    if response.get("outcome") != expected.get("outcome"):
        problems.append(f"outcome {response.get('outcome')} (expected {expected.get('outcome')})")
    text = " ".join(str(response.get(field) or "") for field in ("answer", "message"))
    for forbidden in expected.get("must_not_contain", []) or []:
        if forbidden and re.search(re.escape(forbidden), text, re.IGNORECASE):
            problems.append(f"contains forbidden text {forbidden!r}")
    if expected.get("outcome") == "answered" and response.get("outcome") == "answered":
        problems += fact_problems(text, expected.get("key_facts") or {})
        cited = {c.get("source_file") for c in response.get("citations", [])}
        for source in (expected.get("must_cite") or {}).get("source_files", []) or []:
            if source not in cited:
                problems.append(f"missing citation of {source}")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description="Live evaluation of the demo questions.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--only", help="comma-separated question IDs")
    parser.add_argument(
        "--access-code",
        default=os.environ.get("DEMO_ACCESS_CODE"),
        help="the demo sign-in code, when the deployment sets one (default: $DEMO_ACCESS_CODE)",
    )
    args = parser.parse_args()
    items = json.loads(EXPECTED.read_text(encoding="utf-8"))
    if args.only:
        wanted = set(args.only.split(","))
        items = [item for item in items if item["id"] in wanted]

    passed = 0
    with httpx.Client(base_url=args.base_url, timeout=120) as client:
        tokens: dict[str, str] = {}
        for item in items:
            user = item["user_id"]
            if user not in tokens:
                body = {"user_id": user} | ({"access_code": args.access_code} if args.access_code else {})
                login = client.post("/api/v1/auth/dev-token", json=body)
                login.raise_for_status()
                tokens[user] = login.json()["access_token"]
            response = client.post(
                "/api/v1/query",
                headers={"Authorization": f"Bearer {tokens[user]}"},
                json={"question": item["question"]},
            ).json()
            problems = evaluate(item, response)
            passed += not problems
            confidence = (response.get("confidence") or {}).get("band", "-")
            mark = "PASS" if not problems else "FAIL"
            print(
                f"{mark} {item['id']} {user:20s} {response.get('outcome', '?'):12s} conf={confidence:6s} "
                f"{response.get('latency_ms', 0):>6} ms  {item['question'][:60]}"
            )
            for problem in problems:
                print(f"       - {problem}")
            if problems and response.get("answer"):
                print(f"       answer: {str(response['answer'])[:300]}")
    print(f"\n{passed}/{len(items)} questions passed")
    return 0 if passed == len(items) else 1


if __name__ == "__main__":
    sys.exit(main())
