"""평가 데이터의 메시지를 서버에 하나씩 보내 트레이스를 모은다. 오류 분석에 쓸 트레이스가 적을 때 쓴다.

data/eval/tool_selection.jsonl 41개와 data/eval/safety.jsonl 15개의 message 를 보낸다.
한 턴에 모델을 세 번 부르므로, 먼저 몇 개만 보내 한 턴의 비용을 확인한 뒤 전체를 보낸다.

실행:
  python scripts/send_eval_messages.py                 # 5개만 (기본값)
  python scripts/send_eval_messages.py --limit 56      # 전부
  python scripts/send_eval_messages.py --only safety   # 안전 메시지만

서버는 로그인 없이 받도록 띄운다. .env 가 AUTH=supabase 면 토큰이 없어 401 이 난다.
  PowerShell: $env:AUTH="none"; $env:STORAGE="memory"; uvicorn app.main:app --port 8080

답 내용은 출력하지 않는다. 대화는 개인정보이고, 여기서는 보낸 개수만 알면 된다.
"""
import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FILES = {"tools": ROOT / "data" / "eval" / "tool_selection.jsonl",
         "safety": ROOT / "data" / "eval" / "safety.jsonl"}


def load_messages(only):
    messages = []
    for name, path in FILES.items():
        if only not in (None, name):
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                messages.append((name, json.loads(line)["message"]))
    return messages


def send(base_url, message):
    """한 턴을 보내고 응답 스트림을 끝까지 읽는다. 다 읽어야 기억 정리까지 끝난다."""
    request = urllib.request.Request(
        base_url.rstrip("/") + "/api/chat",
        data=json.dumps({"message": message}, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=120) as response:
        for _ in response:
            pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=5, help="보낼 개수 (기본 5)")
    parser.add_argument("--only", choices=sorted(FILES), help="한 파일만 보낸다")
    parser.add_argument("--base-url", default="http://localhost:8080")
    args = parser.parse_args()

    messages = load_messages(args.only)[:args.limit]
    sent = failed = 0
    for kind, message in messages:
        try:
            send(args.base_url, message)
            sent += 1
        except urllib.error.HTTPError as error:
            failed += 1
            if error.code == 401:
                print("401: 서버가 로그인을 요구합니다. AUTH=none 으로 띄우고 다시 실행하세요.")
                break
            print(f"실패 {kind} HTTP {error.code}")
        except Exception as error:
            failed += 1
            print(f"실패 {kind} {type(error).__name__}")
    print(f"보냄 {sent} / 실패 {failed}")


if __name__ == "__main__":
    main()
