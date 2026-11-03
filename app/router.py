"""메시지마다 쓸 도구를 고르는 도구 선택기.

1. 모델이 메시지와 최근 대화를 읽고 도구를 0~2개 고른다. 확신도(0~1)와 이유를 함께 낸다.
2. 안전장치: 확신도가 기준보다 낮으면 도구를 쓰지 않고 되묻는다(코드 규칙).
fake 모드처럼 모델을 쓰지 않을 때나 모델 응답을 읽지 못했을 때는 키워드 규칙이 같은 형식으로 답한다.
"""
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from app import tracing, when
from app.logs import log
from app.tools import CITIES, TOOLS

CONFIDENCE_THRESHOLD = 0.6
MAX_TOOLS = 2

KEYWORDS = {
    "get_weather": ["날씨", "비 와", "비와", "비 올", "비올", "우산", "더워", "추워", "덥", "춥", "기온", "눈 와", "눈 올", "뭐 입", "쌀쌀", "따뜻"],
    "get_air_quality": ["미세먼지", "초미세", "공기", "마스크", "환기", "황사"],
    "get_holidays": ["공휴일", "휴일", "연휴", "빨간 날", "빨간날", "쉬는 날"],
}
# "토요일 날씨 알려 줘"는 약속이 아니다. 약속은 "~라고 알려 줘", "잊지 않게", "리마인드"처럼 부탁하는 말이 있어야 한다.
ASK_PATTERN = re.compile(r"(라고|으라고|하라고)\s*(꼭\s*)?(알려|말해)|잊지 않게|리마인드|깨워")
CANCEL_WORDS = ["취소", "지워", "없던 걸로", "안 알려 줘도", "안 알려줘도"]
LIST_PATTERN = re.compile(r"(약속|알림|리마인드).*(뭐|목록|있어|있지|있었|보여|확인)")


def tool_lines():
    return "\n".join(f"- {name}: {t['desc']}. 인자 {t['args']}" for name, t in TOOLS.items())


def router_prompt(tools=None):
    """코드에 남겨 두는 기본 문장. Langfuse 에서 못 가져올 때 이것으로 고른다.

    tools 에 "{{tools}}" 를 넣어 부르면 Langfuse 에 올릴 문장이 나온다. 손으로 옮겨 적지 않는다.
    """
    tools = tool_lines() if tools is None else tools
    return "\n".join([
        "너는 AI 친구 서비스에서 사용자 메시지에 답하기 전에 필요한 도구를 고르는 선택기다.",
        "쓸 수 있는 도구:",
        tools,
        "",
        "규칙:",
        "- 도구가 필요 없는 일상 대화면 tools 를 빈 배열로 둔다. 대부분의 메시지는 여기에 해당한다.",
        f"- 꼭 필요한 도구만 최대 {MAX_TOOLS}개 고른다. 묻지 않은 정보의 도구는 덧붙이지 않는다(날씨만 물으면 get_weather 하나).",
        "- 날씨나 미세먼지 이야기를 해도 예보를 묻는 게 아니라 경험이나 하소연이면 도구를 쓰지 않는다.",
        "- location 은 메시지에 도시 이름이 있으면 그 도시, 없으면 사용자 지역이다.",
        "- date 는 [달력]을 보고 정한 YYYY-MM-DD 날짜다. 날짜 표현이 없으면 오늘이다.",
        "- create_reminder 는 사용자가 나중에 알려 달라고 분명히 부탁할 때만 고른다. 일정이 있다는 이야기만 하면 고르지 않는다.",
        "- create_reminder 의 text 는 알려 줄 내용만 짧게 쓴다. 예: '우산 챙기기', '일찍 자기'.",
        "- create_reminder 의 due_at 은 [지금] 시각과 시간대를 기준으로 계산한다. 시각 없이 날짜만 있으면 09:00, 아침 08:00, 점심 12:00, 저녁 19:00, 밤 21:00 으로 한다.",
        "- 최근 대화를 보고 '그럼 모레는?' 같은 이어지는 질문의 도구와 인자를 정한다.",
        "- confidence 는 고른 결과가 맞을 확신을 0과 1 사이 숫자로 쓴다.",
        "- reason 은 고른 이유를 한국어 한 문장으로 쓴다.",
        '- 반드시 JSON 하나로만 답한다: {"tools": [{"name": "도구 이름", "args": {인자}}], "confidence": 0과 1 사이 숫자, "reason": "이유"}',
    ])


@dataclass
class ToolChoice:
    tools: list                          # [{"name": ..., "args": {...}}]
    confidence: float
    reason: str
    decided_by: str                      # model, rule
    action: str = "answer"               # answer, clarify
    guards: list = field(default_factory=list)


# ── 키워드 규칙 ─────────────────────────────────────────────────────

def find_city(text):
    return next((c for c in CITIES if c in text), None)


def reminder_text(text):
    """'목요일 아침에 우산 챙기라고 알려 줘' → '우산 챙기기'처럼 알려 줄 내용만 남긴다."""
    t = re.sub(r"(다음\s*주\s*)?[월화수목금토일]요일|오늘|내일|모레|글피|새벽|아침|오전|점심|오후|저녁|밤", " ", text)
    t = re.sub(r"\d+\s*(분|시간)\s*(뒤|후|있다가)|\d{1,2}\s*시(\s*\d{1,2}\s*분|\s*반)?", " ", t)
    t = re.sub(r"\s*(꼭\s*)?(알려\s*줘|알려\s*주라|알려\s*줄래|리마인드\s*해\s*줘|깨워\s*줘|말해\s*줘)[.!?~ ]*$", "", t)
    t = re.sub(r"^[\s,]*(에는|에)\s+", "", t.strip())
    t = re.sub(r"(으라고|라고)$", "기", t.strip())   # 챙기라고 → 챙기기, 먹으라고 → 먹기
    t = re.sub(r"(다고|하고)$", "", t)
    return re.sub(r"\s+", " ", t).strip(" ,.") or text.strip()


def holiday_range(message, now):
    """'12월에 연휴 있어?' → 그 달의 첫날과 마지막 날. 달이 없으면 빈 인자(앞으로 1년)."""
    m = re.search(r"(\d{1,2})\s*월", message)
    if not m or not 1 <= int(m.group(1)) <= 12:
        return {}
    month = int(m.group(1))
    year = now.year if month >= now.month else now.year + 1
    last = (date(year + month // 12, month % 12 + 1, 1) - timedelta(days=1)).day
    return {"from": f"{year}-{month:02d}-01", "to": f"{year}-{month:02d}-{last}"}


def rule_choose(message, now, location):
    picked = []
    if any(w in message for w in CANCEL_WORDS) and re.search(r"약속|알림|리마인드", message):
        picked.append({"name": "cancel_reminder", "args": {"query": reminder_text(message.replace("취소", ""))}})
    elif LIST_PATTERN.search(message):
        picked.append({"name": "list_reminders", "args": {}})
    elif ASK_PATTERN.search(message) and (due := when.parse_when(message, now)):
        picked.append({"name": "create_reminder", "args": {"text": reminder_text(message), "due_at": due.isoformat()}})
    else:
        day = (now + timedelta(days=when.day_offset(message, now) or 0)).date().isoformat()
        for name, words in KEYWORDS.items():
            if any(w in message for w in words):
                args = {"location": find_city(message) or location, "date": day}
                if name == "get_holidays":
                    args = holiday_range(message, now)
                picked.append({"name": name, "args": args})
    picked = picked[:MAX_TOOLS]
    if not picked:
        return ToolChoice([], 0.8, "도구 키워드가 없는 일상 대화입니다.", "rule")
    labels = ", ".join(TOOLS[p["name"]]["label"] for p in picked)
    return ToolChoice(picked, 0.75, f"'{labels}' 관련 표현이 들어 있습니다.", "rule")


# ── 모델 ────────────────────────────────────────────────────────────

def load_prompt(label=None):
    """(고를 때 쓸 문장, 트레이스에 연결할 프롬프트 객체)를 돌려준다.

    Langfuse 에 닿지 못하거나 변수를 채우지 못하면 코드의 기본 문장으로 돌아간다.
    """
    managed = tracing.prompt("router", label=label)
    if managed is None:
        return router_prompt(), None
    try:
        return managed.compile(tools=tool_lines()), managed
    except Exception as error:
        log("WARNING", "Langfuse 프롬프트를 채우지 못해 코드의 기본 문장을 씁니다", error=type(error).__name__)
        return router_prompt(), None


BACK_WORDS = {"이틀": 2, "사흘": 3, "나흘": 4}


def days_back(text):
    """'전날', '이틀 전'처럼 기준일에서 며칠을 빼라는 표현을 숫자로 바꾼다. 없으면 None."""
    if re.search(r"전날|하루\s*전", text):
        return 1
    m = re.search(r"(이틀|사흘|나흘|\d+)\s*일?\s*전", text)
    if not m:
        return None
    word = m.group(1)
    return BACK_WORDS.get(word) or int(word)


def fix_relative_due(result, message, now):
    """기준일에서 날짜를 빼는 계산을 코드가 대신한다.

    모델은 '금요일'과 '밤' 같은 표현은 잘 읽지만 거기서 하루를 빼는 계산은 자주 틀린다.
    지시문에 계산 방법을 적어도(router 버전 2, 3) 여섯 번 중 한 번만 맞았다.
    요일을 날짜로 바꾸는 일은 when.parse_when 이 [달력]과 같은 방식으로 한다.
    """
    back = days_back(message)
    if back is None:
        return
    base = when.parse_when(message, now)
    if base is None:
        return
    due = (base - timedelta(days=back)).isoformat()
    for t in result.tools:
        if t["name"] == "create_reminder" and t["args"].get("due_at"):
            before = str(t["args"]["due_at"])
            if before[:10] != due[:10]:
                t["args"]["due_at"] = due
                result.guards.append(f"기준일에서 {back}일 앞으로 코드가 다시 계산했습니다: {before} → {due}")


def fix_time_of_day(result, message, now):
    """'아침', '밤' 같은 때 표현의 시각을 코드가 맞춘다. 날짜는 모델이 낸 값을 그대로 둔다.

    when.PARTS 가 '아침 08:00' 처럼 기준을 갖고 있는데 모델은 09:00 으로 내는 일이 있다.
    시각만 바꾸고 날짜는 건드리지 않아, 날짜를 잘 읽은 항목이 깨지지 않게 한다.
    """
    if re.search(r"\d{1,2}\s*시", message):
        return                       # 몇 시라고 분명히 말했으면 그 값을 쓴다
    part = next((p for p in when.PARTS if p in message), None)
    if part is None:
        return
    hour = when.PARTS[part]
    for t in result.tools:
        if t["name"] != "create_reminder" or not t["args"].get("due_at"):
            continue
        raw = str(t["args"]["due_at"])
        try:
            due = datetime.fromisoformat(raw)
        except ValueError:
            continue
        if (due.hour, due.minute) == (hour, 0):
            continue
        t["args"]["due_at"] = due.replace(hour=hour, minute=0).isoformat()
        result.guards.append(f"'{part}' 의 시각을 코드가 맞췄습니다: {raw} → {t['args']['due_at']}")


# '12월', '설날', '올해'처럼 찾을 기간을 말하는 표현. 없으면 기간을 정하지 않는다.
PERIOD_PATTERN = re.compile(r"\d{1,2}\s*월|설날|추석|신정|올해|내년|작년|연말|이번\s*(주|달|해)|다음\s*(주|달|해)")


def fix_holiday_range(result, message):
    """기간을 말하지 않은 공휴일 질문이면 from, to 를 비운다.

    get_holidays 는 인자가 없으면 오늘부터 1년을 찾는다. '다음 공휴일'처럼 기간이 없는 질문에
    모델이 이번 달까지로 좁히는 바람에 다음 공휴일이 빠지곤 했다.
    지시문에 규칙을 더해 봤지만(router 버전 2, 4) 다른 도구에까지 호출을 덧붙이게 만들어 되돌렸다.
    """
    if PERIOD_PATTERN.search(message):
        return
    for t in result.tools:
        if t["name"] != "get_holidays":
            continue
        narrowed = {k: t["args"].pop(k) for k in ("from", "to") if k in t["args"]}
        if narrowed:
            result.guards.append(f"기간을 말하지 않은 질문이라 조회 기간을 비웠습니다: {narrowed}")


def model_choose(message, history, now, location, llm, system=None):
    recent = "\n".join(f"{m['role']}: {m['content']}" for m in history[-4:]) or "(없음)"
    user = (f"[지금] {now.isoformat()} ({when.WEEKDAYS[now.weekday()]}요일)\n[달력]\n{when.calendar_hint(now)}\n"
            f"[사용자 지역] {location}\n[최근 대화]\n{recent}\n\n[메시지]\n{message}")
    data = llm.complete_json(system or router_prompt(), user)
    tools = []
    for t in data.get("tools") or []:
        if isinstance(t, dict) and t.get("name") in TOOLS:
            tools.append({"name": t["name"], "args": t.get("args") if isinstance(t.get("args"), dict) else {}})
    confidence = max(0.0, min(1.0, float(data.get("confidence", 0))))
    result = ToolChoice(tools[:MAX_TOOLS], confidence, str(data.get("reason", "")), "model")

    # 안전장치: '시험 전날'처럼 기준일에서 날짜를 빼는 계산은 코드가 한다
    fix_relative_due(result, message, now)
    fix_time_of_day(result, message, now)
    fix_holiday_range(result, message)

    # 안전장치: 지난 연도로 적힌 날짜는 올해로 고친다
    for t in result.tools:
        for key in ("date", "due_at", "from", "to"):
            if key in t["args"]:
                t["args"][key], fixed = when.fix_year(t["args"][key], now)
                if fixed:
                    result.guards.append(f"{t['name']} 의 {key} 연도가 지난 연도라 {now.year}년으로 고쳤습니다.")
    return result


def choose(message, history, now, location, llm):
    # 규칙이 고를 때는 모델을 부르지 않아 토큰이 0으로 남는다. 계측이 빠진 것과 헷갈리지 않게 decided_by 를 함께 남긴다.
    with tracing.observe("choose-tools", as_type="generation", input=message) as gen:
        if getattr(llm, "can_reason", False):
            system, managed = load_prompt()
            if managed is not None:
                tracing.update(gen, prompt=managed)  # 트레이스에 어느 버전으로 골랐는지 남는다
            try:
                result = model_choose(message, history, now, location, llm, system=system)
            except Exception as error:  # 모델 응답이 형식에 맞지 않으면 규칙으로 대신한다
                log("WARNING", "모델 도구 선택 실패, 키워드 규칙으로 대신함", error=type(error).__name__)
                result = rule_choose(message, now, location)
                result.guards.append("모델 응답을 읽지 못해 키워드 규칙으로 골랐습니다.")
        else:
            result = rule_choose(message, now, location)

        # 안전장치: 같은 도구를 같은 인자로 두 번 고르면 뒤엣것을 버린다.
        # 외부 API를 두 번 부르는 시간과 토큰만 더 든다. 인자가 다르면(두 도시 날씨) 그대로 둔다.
        # 지시문에 "두 번 고르지 말라"고 적어 봤지만 지켜지지 않았고, 오히려 다른 항목에 중복이 생겼다.
        seen, unique = set(), []
        for tool in result.tools:
            key = (tool["name"], json.dumps(tool["args"], sort_keys=True, ensure_ascii=False))
            if key in seen:
                result.guards.append(f"{tool['name']} 을 같은 인자로 두 번 골라 하나만 남겼습니다.")
                continue
            seen.add(key)
            unique.append(tool)
        result.tools = unique

        # 안전장치: 확신이 낮으면 도구를 쓰지 않고 되묻는다
        if result.confidence < CONFIDENCE_THRESHOLD:
            result.action = "clarify"
            result.guards.append(f"확신도 {result.confidence:.2f}가 기준 {CONFIDENCE_THRESHOLD} 미만이라 도구를 쓰지 않고 되묻습니다.")
            result.tools = []

        log("INFO", "도구 선택", tools=[t["name"] for t in result.tools], confidence=round(result.confidence, 2),
            decided_by=result.decided_by, action=result.action)
        tracing.update(gen, output={"tools": result.tools, "reason": result.reason},
                       metadata={"confidence": result.confidence, "decided_by": result.decided_by,
                                 "action": result.action, "guards": result.guards})
        return result
