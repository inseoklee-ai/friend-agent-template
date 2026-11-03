"""tool-selection 데이터셋으로 router 프롬프트 라벨별 실험을 돌린다.

도구 선택은 [지금]과 [달력]을 채우고 도구 목록을 넣는 서비스 코드(model_choose)를 거쳐야 해서
화면 실험으로는 돌릴 수 없다. 그래서 SDK 의 run_experiment 에 실행할 작업과 코드 평가자를 넘긴다.

같은 라벨을 두 번씩 돌린다. 같은 프롬프트도 실행마다 맞힌 수가 흔들려,
한 번씩 돌린 결과로는 차이가 우연인지 가릴 수 없기 때문이다.

실행:
  python scripts/run_tool_experiments.py --labels production staging --runs 2
  python scripts/run_tool_experiments.py --labels staging --runs 1 --limit 5   # 소량 시험
"""
import argparse
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import load_settings  # noqa: E402
from app.llm import make_llm  # noqa: E402
from app.router import load_prompt, model_choose  # noqa: E402
from app.tracing import client  # noqa: E402
from scripts.eval_tools import LOCATION, NOW, args_match, covers_match  # noqa: E402

DATASET = "tool-selection"


def make_task(llm, system):
    def task(*, item, **kwargs):
        row = item.input if isinstance(item.input, dict) else {}
        choice = model_choose(row.get("message", ""), row.get("history", []), NOW, LOCATION, llm, system=system)
        return {"tools": [{"name": t["name"], "args": t["args"]} for t in choice.tools],
                "confidence": choice.confidence, "reason": choice.reason}
    return task


def tools_right(expected, output):
    """고른 도구가 정답 집합과 맞는지. 선택 도구는 더 골라도 된다."""
    picked = {t["name"] for t in output.get("tools", [])}
    required = set(expected.get("tools") or [])
    optional = set(expected.get("optional_tools") or [])
    return required <= picked <= required | optional


def evaluate(*, input, output, expected_output, metadata, **kwargs):
    """eval_tools.py 와 같은 기준으로 채점한다. 통과/실패 두 점수를 낸다."""
    expected = expected_output or {}
    if not isinstance(output, dict):
        return [{"name": "tool-correct", "value": 0}, {"name": "args-correct", "value": 0}]
    right = tools_right(expected, output)
    wrong = []
    if right:
        got = {t["name"]: t["args"] for t in output.get("tools", [])}
        for name, want in (expected.get("args") or {}).items():
            wrong += args_match(want, got.get(name, {}))
        if expected.get("next_holiday"):
            wrong += covers_match(expected["next_holiday"], got.get("get_holidays", {}))
    return [
        {"name": "tool-correct", "value": 1 if right else 0,
         "comment": "" if right else f"고른 도구 {sorted(t['name'] for t in output.get('tools', []))}"},
        {"name": "args-correct", "value": 1 if right and not wrong else 0,
         "comment": "; ".join(wrong)},
    ]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--labels", nargs="+", default=["production", "staging"])
    parser.add_argument("--runs", type=int, default=2)
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()

    settings = load_settings()
    llm = make_llm(settings)
    langfuse = client()
    if langfuse is None:
        print("Langfuse 키가 없어 실험을 돌릴 수 없습니다.")
        return

    items = langfuse.get_dataset(DATASET).items
    if args.limit:
        items = items[:args.limit]
    stamp = datetime.now().strftime("%m%d-%H%M")

    for label in args.labels:
        system, managed = load_prompt(label=label)
        version = getattr(managed, "version", "코드 기본 문장")
        for n in range(1, args.runs + 1):
            # 같은 run_name 이면 Langfuse 가 한 실험으로 묶는다. 앞서 돌린 것과 섞이지 않게 시각을 붙인다.
            run_name = f"router-{label}-v{version}-{n} {stamp}"
            result = langfuse.run_experiment(
                name=run_name, run_name=run_name,
                description=f"router 프롬프트 {label} 라벨(버전 {version}) · {n}회차",
                data=items, task=make_task(llm, system), evaluators=[evaluate],
                max_concurrency=4, metadata={"label": label, "prompt_version": version})
            scores = {}
            for item in result.item_results:
                for ev in item.evaluations:
                    name = ev.name if hasattr(ev, "name") else ev["name"]
                    value = ev.value if hasattr(ev, "value") else ev["value"]
                    scores.setdefault(name, []).append(value)
            summary = " · ".join(f"{k} {int(sum(v))}/{len(v)}" for k, v in sorted(scores.items()))
            print(f"{run_name}: {summary}")
    langfuse.flush()


if __name__ == "__main__":
    main()
