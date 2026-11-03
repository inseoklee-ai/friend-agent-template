"""도구 선택 평가. data/eval/tool_selection.jsonl 의 메시지마다 고른 도구와 핵심 인자가 정답과 맞는지 센다.

실행:
  python scripts/eval_tools.py            # .env 의 LLM 으로 모델 선택기를 평가 (호출 40번, 비용 발생)
  python scripts/eval_tools.py --rule     # 키워드 규칙 선택기를 평가 (비용 없음)

정답의 날짜와 시각은 '2026-10-12(월) 14:00 한국 시간'을 지금으로 보고 만들었다. 평가도 그 시각을 지금으로 쓴다.
"""
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import load_settings  # noqa: E402
from app.llm import make_llm  # noqa: E402
from app.router import load_prompt, model_choose, rule_choose  # noqa: E402

NOW = datetime.fromisoformat("2026-10-12T14:00:00+09:00")
LOCATION = "서울"


def args_match(expected, actual):
    """정답에 적힌 인자만 비교한다. due_at 은 분 단위까지 같은 시각이면 맞다."""
    wrong = []
    for key, want in expected.items():
        got = actual.get(key)
        if key == "due_at":
            try:
                same = datetime.fromisoformat(str(got)).replace(tzinfo=None, second=0) == datetime.fromisoformat(want)
            except ValueError:
                same = False
        elif key in ("from", "to"):
            same = str(got or "")[:7] == want[:7]   # 월까지만 비교
        elif key == "day":   # 정답은 '오늘로부터 며칠 뒤', 결과는 date(YYYY-MM-DD)
            try:
                same = (datetime.fromisoformat(str(actual.get("date"))).date() - NOW.date()).days == want
            except ValueError:
                same = False
            got = actual.get("date")
        else:
            same = want in str(got or "")
        if not same:
            wrong.append(f"{key}: 정답 {want}, 결과 {got}")
    return wrong


def covers_match(want, actual):
    """기간 인자가 그 날짜를 품는지 본다.

    기간을 기대 결과에 그대로 적으면 맞는 답도 틀린 것으로 센다. 인자를 비워 도구가
    1년을 찾게 하는 것도 올바른 방법이기 때문이다. 확인할 것은 '그 기간 안에
    다음 공휴일이 들어가는가' 하나다.
    """
    start, end = str(actual.get("from") or ""), str(actual.get("to") or "")
    if not start and not end:
        return []                      # 비워 두면 도구가 1년을 찾는다
    if (start or "0000-01-01") <= want <= (end or "9999-12-31"):
        return []
    return [f"기간에 {want} 가 빠짐: from {start or '없음'}, to {end or '없음'}"]


def main():
    use_rule = "--rule" in sys.argv
    label = None
    if "--label" in sys.argv:
        label = sys.argv[sys.argv.index("--label") + 1]
    llm = None if use_rule else make_llm(load_settings())
    if llm is not None and not llm.can_reason:
        print("LLM_PROVIDER=fake 에서는 모델 평가를 할 수 없습니다. --rule 로 실행하세요.")
        return

    # --label 로 Langfuse 의 어느 버전으로 평가할지 고른다. 없으면 ENV 에 맞는 라벨
    system = None if use_rule else load_prompt(label=label)[0]
    rows = [json.loads(line) for line in (ROOT / "data/eval/tool_selection.jsonl").read_text(encoding="utf-8").splitlines()]
    tool_ok = args_ok = 0
    failures = []
    for row in rows:
        if use_rule:
            choice = rule_choose(row["message"], NOW, LOCATION)
        else:
            choice = model_choose(row["message"], row["history"], NOW, LOCATION, llm, system=system)
        picked = {t["name"] for t in choice.tools}
        required, optional = set(row["expected_tools"]), set(row["optional_tools"])
        tools_right = required <= picked <= required | optional
        wrong = []
        if tools_right:
            tool_ok += 1
            got = {t["name"]: t["args"] for t in choice.tools}
            for name, expected in row["expected_args"].items():
                wrong += args_match(expected, got.get(name, {}))
            for name, day in row.get("expected_covers", {}).items():
                wrong += covers_match(day, got.get(name, {}))
            if not wrong:
                args_ok += 1
        if not tools_right or wrong:
            failures.append((row, sorted(picked), wrong, choice.confidence))

    n = len(rows)
    print(f"[{'키워드 규칙' if use_rule else '모델 ' + llm.model}] 도구 정답 {tool_ok}/{n}, 도구와 인자 모두 정답 {args_ok}/{n}")
    for row, picked, wrong, conf in failures:
        detail = "; ".join(wrong) if wrong else f"정답 {row['expected_tools'] or '도구 없음'}, 결과 {picked or '도구 없음'}"
        print(f"  {row['id']} {row['message']}  →  {detail}  (확신도 {conf:.2f})")


if __name__ == "__main__":
    main()
