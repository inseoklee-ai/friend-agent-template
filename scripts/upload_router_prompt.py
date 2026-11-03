"""도구 선택 지시문을 Langfuse 프롬프트 관리로 올린다.

app/router.py 의 router_prompt 를 그대로 부르되 도구 목록 자리에 {{tools}} 를 넣는다.
손으로 옮겨 적으면 코드와 어긋나고, 실험 결과가 달랐을 때 지시문 탓인지 옮겨 적은 탓인지 가릴 수 없다.

실행:
  python scripts/upload_router_prompt.py --show                 # 올리지 않고 내용만 본다
  python scripts/upload_router_prompt.py --labels production staging
  python scripts/upload_router_prompt.py --file v2.txt --labels staging   # 파일 내용을 새 버전으로
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import load_settings  # noqa: E402
from app.router import router_prompt  # noqa: E402
from app.tracing import client  # noqa: E402

NAME = "router"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--labels", nargs="*", default=["production", "staging"])
    parser.add_argument("--file", help="이 파일의 내용을 새 버전으로 올린다. 없으면 코드의 문장")
    parser.add_argument("--show", action="store_true")
    args = parser.parse_args()

    load_settings()
    text = Path(args.file).read_text(encoding="utf-8") if args.file else router_prompt("{{tools}}")
    if args.show:
        print(text)
        return

    langfuse = client()
    if langfuse is None:
        print("Langfuse 키가 없어 올리지 않았습니다.")
        return
    prompt = langfuse.create_prompt(name=NAME, type="text", prompt=text, labels=args.labels,
                                    commit_message="app/router.py 의 router_prompt")
    langfuse.flush()
    print(f"{NAME} 버전 {prompt.version} 올림 · 라벨 {args.labels}")


if __name__ == "__main__":
    main()
