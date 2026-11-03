"""실험용 답 지시문을 Langfuse 프롬프트로 올린다.

app/agent.py 의 system_prompt 가 만드는 문장을 그대로 쓴다. 손으로 옮겨 적으면
서비스와 어긋나고, 답이 틀렸을 때 지시문 탓인지 옮겨 적은 탓인지 가릴 수 없다.

[지금] 을 데이터셋의 고정 도구 결과와 같은 날(2026-11-02 월 14:00)로 둔다.
"이번 주", "다음 공휴일" 이 가리키는 날짜가 도구 결과와 맞아야 하기 때문이다.

실행:
  python scripts/upload_reply_prompt.py              # holiday-reply-answer 에 새 버전을 만든다
  python scripts/upload_reply_prompt.py --show       # 올리지 않고 내용만 본다
"""
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.agent import system_prompt  # noqa: E402
from app.config import load_settings  # noqa: E402
from app.persona import DEFAULT_CHARACTER  # noqa: E402
from app.tracing import client  # noqa: E402

NAME = "holiday-reply-answer"
NOW = datetime(2026, 11, 2, 14, 0, tzinfo=ZoneInfo("Asia/Seoul"))


def build():
    """변수 이름은 데이터셋 항목 input 의 키(message, tool_result)와 같아야 값이 채워진다."""
    system = system_prompt(DEFAULT_CHARACTER, NOW, "서울", [], None, ["{{tool_result}}"])
    return [{"role": "system", "content": system},
            {"role": "user", "content": "{{message}}"}]


def main():
    load_settings()
    messages = build()
    if "--show" in sys.argv:
        for message in messages:
            print(f"[{message['role']}]\n{message['content']}\n")
        return

    langfuse = client()
    if langfuse is None:
        print("Langfuse 키가 없어 올리지 않았습니다.")
        return
    prompt = langfuse.create_prompt(name=NAME, type="chat", prompt=messages, labels=["latest"],
                                    commit_message="app/agent.py 의 system_prompt 를 그대로 옮김")
    langfuse.flush()
    print(f"{NAME} 버전 {prompt.version} 올림")


if __name__ == "__main__":
    main()
