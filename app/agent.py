"""메시지 하나를 처리하는 흐름(한 턴).

① 안전장치: 위기 표현이면 정해진 안전 응답으로 끝낸다(코드 규칙).
② 도구 선택: 모델이 도구를 0~2개 고른다. 확신이 낮으면 되묻는다(router.py).
③ 도구 실행: 날씨, 미세먼지, 공휴일, 약속(tools.py).
④ 답 생성: 캐릭터 지시문 + 기억 + 도구 결과 + 최근 대화로 답을 스트리밍한다.
⑤ 기억 정리: 답을 다 보낸 뒤 같은 요청 안에서 기억 메모와 대화 요약을 갱신한다(memory.py).
   Cloud Run 기본 설정(요청 기반 과금)에서는 응답이 끝난 뒤 CPU가 거의 주어지지 않아서,
   응답 뒤 백그라운드로 돌리면 기억 정리가 멈추거나 늦어진다. 그래서 응답을 닫기 전에 처리한다.

결과는 이벤트(dict)로 하나씩 내보낸다. show_panel 이 꺼져 있으면(production) 개발용 이벤트는 아예 만들지 않는다.
"""
import time
from datetime import datetime, timedelta, timezone

from app import memory, tracing, when
from app.logs import log
from app.persona import DEFAULT_CHARACTER, build_persona
from app.router import choose
from app.safety import CRISIS_REPLY, is_crisis
from app.tools import TOOLS, Context, run_tool

GREETING_GAP = timedelta(hours=6)  # 마지막 대화 뒤 이만큼 지나면 앱을 열 때 먼저 말을 건다


def system_prompt(character, now, location, memories, summary, tool_lines=(), clarify=False):
    parts = [build_persona(character),
             f"[지금] {now.year}년 {when.label(now)} ({now.tzinfo}), 사용자 지역: {location}\n[달력]\n{when.calendar_hint(now)}"]
    block = memory.memory_block(memories, summary)
    if block:
        parts.append(block)
    if tool_lines:
        parts.append("[도구 결과]\n" + "\n".join(f"- {line}" for line in tool_lines))
    if clarify:
        parts.append("[되묻기]\n사용자가 무엇을 원하는지 확실하지 않다. 짧게 되물어서 확인한다.")
    return "\n\n".join(parts)


def run_turn(message, uid, llm, store, show_panel):
    """한 턴 전체를 chat-turn 트레이스 하나로 남긴다.

    _run_turn 은 제너레이터라서 다 소비해야 기억 정리까지 끝난다.
    여기서 끝까지 돌려준 뒤에 트레이스를 닫아야 memory-update 가 트레이스 안에 들어간다.
    tracing.follow 는 조각마다 스레드가 바뀌어도 단계가 트레이스 안에 남게 한다.
    """
    try:
        yield from tracing.follow(_traced_turn(message, uid, llm, store, show_panel))
    finally:
        tracing.flush()


def _traced_turn(message, uid, llm, store, show_panel):
    seen = {}  # _run_turn 이 채운다. 트레이스에 붙여 나중에 걸러 보려는 값
    with tracing.attributes(user_id=uid, session_id=tracing.session_id(uid),
                            metadata={"commit": tracing.commit()}, **tracing.deployment()), \
            tracing.observe("chat-turn", input=message) as turn:
        reply = ""
        message_id = None
        for event in _run_turn(message, uid, llm, store, show_panel, seen):
            if event["type"] == "delta":
                reply += event["text"]
            elif event["type"] == "done":
                message_id = event["message_id"]
            yield event
        tracing.update(turn, output=reply,
                       metadata={"commit": tracing.commit(), "message_id": message_id, **seen})


def _run_turn(message, uid, llm, store, show_panel, seen):
    settings = store.get_settings(uid)
    character = store.get_character(uid) or DEFAULT_CHARACTER
    now = when.now_in(settings["timezone"])
    memories, summary, recent = memory.build_context(store, uid)
    history = [{"role": m["role"], "content": m["content"]} for m in recent]
    store.add_message(uid, "user", message)

    # ① 위기 표현
    with tracing.observe("safety", input=message) as span:
        crisis = is_crisis(message)
        tracing.update(span, output={"crisis": crisis})
    seen["safety"] = crisis
    if crisis:
        guards = ["위기 표현이 있어 캐릭터 설정과 상관없이 정해진 안전 응답을 보냈습니다."]
        if show_panel:
            yield {"type": "turn", "tools": [], "confidence": None, "reason": "", "decided_by": "rule",
                   "action": "safety", "guards": guards}
        yield {"type": "delta", "text": CRISIS_REPLY}
        saved = store.add_message(uid, "assistant", CRISIS_REPLY,
                                  {"action": "safety", "guards": guards,
                                   "trace_id": tracing.current_trace_id()})
        log("WARNING", "위기 표현 안전 응답")
        yield {"type": "done", "message_id": saved["id"]}
        return

    # ② 도구 선택
    choice = choose(message, history, now, settings["location"], llm, uid=uid)
    if show_panel:
        yield {"type": "turn", "tools": [{"name": t["name"], "label": TOOLS[t["name"]]["label"], "args": t["args"]}
                                         for t in choice.tools],
               "confidence": choice.confidence, "reason": choice.reason, "decided_by": choice.decided_by,
               "action": choice.action, "guards": choice.guards}
    seen.update(tools=[t["name"] for t in choice.tools], confidence=choice.confidence,
                decided_by=choice.decided_by, action=choice.action)
    if choice.variant:
        seen["ab_variant"] = choice.variant   # Tracing 화면에서 그룹별로 걸러 보려고 남긴다

    # 태그는 관찰 단위를 만들 때만 붙으므로, 고른 도구를 안 뒤부터 걸어 준다
    tags = [f"tool:{t['name']}" for t in choice.tools] or ["no-tool"]
    if choice.action == "clarify":
        tags.append("clarify")
    with tracing.attributes(tags=tags):
        # ③ 도구 실행
        ctx = Context(store, uid, settings["location"], settings["timezone"])
        results = [run_tool(t["name"], t["args"], ctx) for t in choice.tools]
        seen["tool_failed"] = any(not r.ok for r in results)
        for r in results:
            if show_panel:
                yield {"type": "tool", "name": r.name, "label": TOOLS[r.name]["label"], "ok": r.ok,
                       "summary": r.summary, "data": r.data}
            if r.ok and r.name in ("create_reminder", "cancel_reminder"):
                yield {"type": "reminder", "action": r.name, "summary": r.summary}

        # ④ 답 생성
        system = system_prompt(character, now, settings["location"], memories, summary,
                               [r.summary for r in results], clarify=choice.action == "clarify")
        if show_panel:
            yield {"type": "context", "memories": [m["text"] for m in memories],
                   "summary": summary["text"] if summary else None, "recent_messages": len(history)}
        sent = [{"role": "system", "content": system}, *history, {"role": "user", "content": message}]
        reply = ""
        with tracing.observe("reply", as_type="generation", input=sent) as gen:
            started = time.monotonic()
            first_at = None
            first_ms = None
            for chunk in llm.chat_stream(sent):
                if first_at is None:
                    # 답을 스트리밍으로 보내므로 사용자가 기다린 시간은 첫 글자가 나온 시각까지다.
                    # completion_start_time 에 넣어야 화면의 Time To First Token 이 채워진다.
                    first_at = datetime.now(timezone.utc)
                    first_ms = round((time.monotonic() - started) * 1000)
                reply += chunk
                yield {"type": "delta", "text": chunk}
            tracing.update(gen, output=reply, completion_start_time=first_at,
                           metadata={"first_token_ms": first_ms})
        # trace_id 를 함께 저장해 둬야, 나중에 화면에서 누른 반응을 그 트레이스에 점수로 붙일 수 있다
        meta = {"tools": [t["name"] for t in choice.tools], "confidence": choice.confidence,
                "action": choice.action, "guards": choice.guards,
                "trace_id": tracing.current_trace_id()}
        saved = store.add_message(uid, "assistant", reply, meta)

        # ⑤ 기억 정리
        yield {"type": "memory_start"}
        changes = memory.update_memories(store, uid, message, reply, now, llm)
        memory.maybe_summarize(store, uid, llm)
        yield {"type": "memory", "added": [m["text"] for m in changes["added"]],
               "updated": [m["text"] for m in changes["updated"]], "removed": [m["text"] for m in changes["removed"]]}
        yield {"type": "done", "message_id": saved["id"]}


def greet(uid, llm, store):
    """앱을 열 때 먼저 말을 건다. 때가 된 약속이 있거나, 대화가 처음이거나, 오래 쉬었을 때만.

    약속은 '전달됨'으로 바꾸는 데 성공한 것만 전한다. 창을 두 개 열어도 같은 약속을 두 번 전하지 않는다.
    """
    settings = store.get_settings(uid)
    character = store.get_character(uid) or DEFAULT_CHARACTER
    now = when.now_in(settings["timezone"])
    due = store.claim_due_reminders(uid, now)
    last = store.recent_messages(uid, 1)
    if not due and last and now - last[-1]["created_at"] < GREETING_GAP:
        return None

    memories, summary, _ = memory.build_context(store, uid)
    lines = [f"약속 알림: '{r['text']}' ({when.label(r['due_at'].astimezone(now.tzinfo))} 예정이었음)" for r in due]
    system = system_prompt(character, now, settings["location"], memories, summary, lines)
    guide = ["사용자가 방금 앱을 열었다. 네가 먼저 1~2문장으로 말을 건다."]
    guide.append("[도구 결과]의 약속 알림을 꼭 전한다." if due else "전할 약속은 없다. 약속이나 알림 이야기를 꺼내지 않는다.")
    guide.append("기억 메모에 오늘이거나 이미 지난 일정이 있으면 어땠는지 물어본다.")
    if not last:
        guide.append("처음 만난 사이다. 이름을 넣어 짧게 자기소개를 한다.")
    system += "\n\n[먼저 말 걸기]\n" + " ".join(guide)
    sent = [{"role": "system", "content": system},
            {"role": "user", "content": "(앱을 열었다. 먼저 말을 걸어 줘)"}]
    # /api/greeting 은 /api/chat 과 다른 요청이라 트레이스를 따로 만든다. 비용을 보려고 generation 으로 둔다.
    with tracing.attributes(user_id=uid, session_id=tracing.session_id(uid), tags=["greeting"],
                            metadata={"commit": tracing.commit()}, **tracing.deployment()), \
            tracing.observe("greeting", as_type="generation", input=sent) as gen:
        text = llm.complete_text(sent)
        tracing.update(gen, output=text, metadata={"reminders": len(due)})
    tracing.flush()
    saved = store.add_message(uid, "assistant", text, {"action": "greeting", "reminders": [r["id"] for r in due]})
    log("INFO", "먼저 말 걸기", reminders=len(due))
    return {"id": saved["id"], "text": text, "reminders": [r["text"] for r in due]}
