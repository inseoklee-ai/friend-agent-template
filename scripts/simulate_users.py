"""여러 사용자 유형의 대화를 서버에 보내 트레이스를 모은다. 트레이스와 피드백이 적을 때 쓴다.

실제 개인정보는 쓰지 않는다. 대화마다 정정하기, 같은 질문 다시 하기, 짧게 끊기 같은
실제 사용자 행동을 섞어서, 로그에서 암묵적 피드백을 찾을 수 있게 한다.

실행 (서버는 로그인 없이, 환경 이름을 따로 두어 실제 기록과 나눈다):
  PowerShell: $env:AUTH="none"; $env:STORAGE="memory"; $env:ENV="sim"; uvicorn app.main:app --port 8080
  python scripts/simulate_users.py --limit 1   # 대화 하나만 먼저 보내 비용 확인
  python scripts/simulate_users.py             # 전부

답 내용은 출력하지 않는다. 보낸 턴 수만 출력한다.
"""
import argparse

from send_eval_messages import send

CONVERSATIONS = {
    "바쁜 직장인": [
        "오늘 회의만 4개야 진짜 지친다",
        "내일 오후 3시에 팀장님 보고 있는 거 알려줘",
        "아니 3시 말고 4시로 바꿔줘",
        "내일 서울 날씨 어때?",
        "우산 챙겨야 해?",
        "ㅇㅋ 고마워",
    ],
    "짧게 말하는 사람": [
        "ㅎㅇ",
        "심심",
        "뭐해",
        "ㅋㅋ",
        "배고파",
        "ㅂㅂ",
    ],
    "고민 상담": [
        "요즘 친구랑 사이가 좀 멀어진 것 같아",
        "내가 먼저 연락을 안 해서 그런가",
        "그런 얘기 말고 그냥 들어줬으면 좋겠어",
        "어제도 단톡에서 나만 답장 못 받았어",
        "좀 서운하다",
        "들어줘서 고마워",
    ],
    "기억 확인": [
        "나 고양이 키워 이름은 보리야",
        "나는 매운 음식 잘 못 먹어",
        "주말에 뭐 먹을지 추천해줘",
        "내가 매운 거 못 먹는다고 했잖아",
        "우리 고양이 이름 기억나?",
        "다음 주 토요일에 보리 병원 가는 거 기억해줘",
    ],
    "일정 많이 묻는 사람": [
        "다음 주 월요일 아침 9시에 치과 예약 알려줘",
        "이번 주 금요일 저녁 7시 동창 모임도",
        "내가 잡아둔 약속 뭐 있지?",
        "금요일 모임 몇 시였지?",
        "모레 날씨 알려줘",
        "크리스마스가 무슨 요일이야?",
    ],
    "주제 자주 바꾸는 사람": [
        "요즘 볼만한 드라마 있어?",
        "갑자기 운동하고 싶다",
        "내일 부산 날씨는?",
        "아 그리고 3일 뒤 오후 2시에 미용실 알려줘",
        "아까 말한 드라마 다시 말해줘",
        "오늘 하루 어땠어?",
    ],
    "되묻는 사람": [
        "다음 달 첫째 주 수요일 점심 약속 잡아줘",
        "그게 며칠이야?",
        "아니 그날 말고 둘째 주 수요일",
        "제대로 된 거 맞아?",
        "오늘 기분 별로야",
        "그냥 아무 말이나 해줘",
    ],
    "말투 민감한 사람": [
        "안녕 오늘 처음 와봤어",
        "너는 누구야?",
        "말이 너무 길어 짧게 말해줘",
        "추천할 만한 책 있어?",
        "또 길다",
        "됐어 나중에 올게",
    ],
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=len(CONVERSATIONS), help="보낼 대화 수")
    parser.add_argument("--base-url", default="http://localhost:8080")
    args = parser.parse_args()

    sent = failed = 0
    for name, turns in list(CONVERSATIONS.items())[:args.limit]:
        for message in turns:
            try:
                send(args.base_url, message)
                sent += 1
            except Exception as error:
                failed += 1
                print(f"실패 {name} {type(error).__name__}")
    print(f"보냄 {sent} / 실패 {failed}")


if __name__ == "__main__":
    main()
