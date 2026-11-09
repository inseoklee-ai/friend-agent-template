"""date-reply 데이터셋으로 답 생성(④)의 날짜 실험을 돌린다.

data/eval/date_reply.jsonl 을 Langfuse 데이터셋으로 올리고(같은 id 는 덮어씀),
지금 코드의 system_prompt 로 답을 만들어 코드 평가자로 채점한다.
고치기 전과 후에 각각 실행해 run 이름으로 나눠 비교한다.

통과 조건(항목마다):
- 답에 나온 'NNNN년' 연도가 2026 이거나 항목의 allow_years 에 있어야 한다.
- must_include 는 모두, must_include_any 는 하나 이상 들어 있어야 한다.
- must_exclude 는 하나도 없어야 한다.

실행:
  python scripts/run_date_experiment.py --name before --runs 2
  python scripts/run_date_experiment.py --name after --runs 2
"""
import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.agent import system_prompt  # noqa: E402
from app.config import load_settings  # noqa: E402
from app.llm import make_llm  # noqa: E402
from app.persona import DEFAULT_CHARACTER  # noqa: E402
from app.tracing import client  # noqa: E402

DATASET = "date-reply"
NOW = datetime(2026, 11, 9, 11, 0, tzinfo=ZoneInfo("Asia/Seoul"))
ROWS = [json.loads(line) for line in (ROOT / "data" / "eval" / "date_reply.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()]


def check(row, reply):
    """통과하면 빈 목록, 실패하면 이유 목록."""
    why = []
    allowed = {str(NOW.year), *row.get("allow_years", [])}
    wrong = sorted(set(re.findall(r"(\d{4})\s*년", reply)) - allowed)
    if wrong:
        why.append(f"틀린 연도 {wrong}")
    why += [f"'{w}' 없음" for w in row.get("must_include", []) if w not in reply]
    any_of = row.get("must_include_any")
    if any_of and not any(w in reply for w in any_of):
        why.append(f"{any_of} 중 하나도 없음")
    why += [f"'{w}' 있음" for w in row.get("must_exclude", []) if w in reply]
    return why


def upload(langfuse):
    langfuse.create_dataset(name=DATASET, description="답 생성의 날짜·연도. 문제 12, 정상 12")
    for row in ROWS:
        langfuse.create_dataset_item(dataset_name=DATASET, id=f"{DATASET}-{row['id']}", input=row,
                                     metadata={"id": row["id"], "kind": row["kind"], "note": row["note"]})


def make_task(llm):
    def task(*, item, **kwargs):
        row = item.input
        system = system_prompt(DEFAULT_CHARACTER, NOW, "서울", [], None, row.get("tool_lines", ()))
        sent = [{"role": "system", "content": system}, *row["history"], {"role": "user", "content": row["message"]}]
        return "".join(llm.chat_stream(sent))
    return task


def evaluate(*, input, output, **kwargs):
    why = check(input, output or "")
    return [{"name": "date-reply-pass", "value": 0 if why else 1, "comment": "; ".join(why)}]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", required=True, help="before, after 처럼 run 이름 앞부분")
    parser.add_argument("--runs", type=int, default=2)
    args = parser.parse_args()

    llm = make_llm(load_settings())
    langfuse = client()
    if langfuse is None:
        print("Langfuse 키가 없어 실험을 돌릴 수 없습니다.")
        return
    upload(langfuse)
    items = langfuse.get_dataset(DATASET).items
    stamp = datetime.now().strftime("%m%d-%H%M")
    for n in range(1, args.runs + 1):
        run_name = f"date-{args.name}-{n} {stamp}"
        result = langfuse.run_experiment(name=run_name, run_name=run_name, data=items, task=make_task(llm),
                                         evaluators=[evaluate], max_concurrency=4, metadata={"variant": args.name})
        passed = {"문제": [0, 0], "정상": [0, 0]}
        for r in result.item_results:
            kind = r.item.input["kind"]
            ok = all((e.value if hasattr(e, "value") else e["value"]) == 1 for e in r.evaluations)
            passed[kind][0] += ok
            passed[kind][1] += 1
            if not ok:
                print(f"  실패 {r.item.input['id']}: {r.evaluations[0].comment}")
        print(f"{run_name}: 문제 {passed['문제'][0]}/{passed['문제'][1]} · 정상 {passed['정상'][0]}/{passed['정상'][1]}")
    langfuse.flush()


if __name__ == "__main__":
    main()
