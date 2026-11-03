"""data/eval/ 의 평가 데이터를 Langfuse 데이터셋으로 올린다.

항목 id 를 '<데이터셋>-<항목 id>' 로 고정해 보내므로, 다시 실행해도 항목이 늘지 않고 덮어쓴다.

실행:
  python scripts/upload_datasets.py

키는 .env 의 LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, LANGFUSE_BASE_URL 에서 읽는다.
키가 없으면 아무것도 하지 않는다. 끝나면 데이터셋별 항목 수만 출력한다.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import load_settings  # noqa: E402  (.env 를 읽는다)
from app.tracing import client  # noqa: E402

EVAL = ROOT / "data" / "eval"


def rows(name):
    return [json.loads(line) for line in (EVAL / name).read_text(encoding="utf-8").splitlines() if line.strip()]


def tool_selection():
    for row in rows("tool_selection.jsonl"):
        expected = {"tools": row["expected_tools"], "args": row["expected_args"]}
        if row.get("optional_tools"):
            expected["optional_tools"] = row["optional_tools"]
        for day in row.get("expected_covers", {}).values():
            expected["next_holiday"] = day   # 기간이 이 날짜를 품어야 한다
        yield row["id"], {"message": row["message"], "history": row["history"]}, expected, {
            "id": row["id"], "category": "tool-selection", "note": row.get("note", "")}


def memory_extraction():
    for row in rows("memory_extraction.jsonl"):
        yield row["id"], {"conversation": row["conversation"]}, {
            "must_include": row["must_include"], "must_not_include": row["must_not_include"]}, {
            "id": row["id"], "category": "memory-extraction"}


def safety():
    for row in rows("safety.jsonl"):
        yield row["id"], {"message": row["message"]}, {"expected": row["expected"]}, {
            "id": row["id"], "category": row["category"]}


def holiday_reply():
    for row in rows("holiday_reply.jsonl"):
        yield row["id"], {"message": row["message"], "tool_result": row["tool_result"]}, {
            "pass_condition": row["pass_condition"]}, {
            "id": row["id"], "category": "failure" if row["kind"] == "실패 사례" else "normal",
            "note": row.get("note", "")}


DATASETS = {
    "tool-selection": ("도구 선택. 고른 도구와 인자가 정답과 맞는지 본다.", tool_selection),
    "memory-extraction": ("기억 정리. 뽑은 기억에 꼭 들어갈 말과 들어가면 안 되는 말을 본다.", memory_extraction),
    "safety": ("안전. 위기 표현에 정해진 안전 응답이 나가는지 본다.", safety),
    "holiday-reply": ("답 만들기. 도구 결과를 고정해 넣고 답만 판정한다.", holiday_reply),
}


def main():
    load_settings()
    langfuse = client()
    if langfuse is None:
        print("Langfuse 키가 없어 올리지 않았습니다.")
        return

    for name, (description, build) in DATASETS.items():
        langfuse.create_dataset(name=name, description=description)
        count = 0
        for item_id, item_input, expected, metadata in build():
            langfuse.create_dataset_item(dataset_name=name, id=f"{name}-{item_id}",
                                         input=item_input, expected_output=expected, metadata=metadata)
            count += 1
        print(f"{name}: {count}개")
    langfuse.flush()


if __name__ == "__main__":
    main()
