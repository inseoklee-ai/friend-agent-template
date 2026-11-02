# Langfuse 계측 구조

섹션 5에서 코딩 에이전트에게 건넬 계측 구조다. 코드(app/agent.py 의 run_turn, greet)를 읽고 확인한 내용이다.

## 관찰 단위

| 단계 | 이름 | 종류 | 남길 것 | 코드 위치 |
|---|---|---|---|---|
| 한 턴 전체 | `chat-turn` | 트레이스 | 사용자 메시지, 최종 답, 메시지 ID | `app/agent.py` `run_turn` |
| 안전장치 | `safety` | span | 위기 표현 감지 여부 | `app/safety.py` `is_crisis` |
| 도구 선택 | `choose-tools` | generation | 모델 입력과 출력, 고른 도구, 확신도, `decided_by`(model/rule), `action` | `app/router.py` `choose` |
| 도구 실행 | `tool:<도구 이름>` | span | 인자, 결과 요약, `ok` | `app/tools.py` `run_tool` (도구마다 하나씩) |
| 답 만들기 | `reply` | generation | 모델에 보낸 메시지, 답, 토큰, 첫 글자가 나온 시각 | `app/agent.py` `llm.chat_stream` 부분 |
| 기억 정리 | `memory-update` | generation | 추가, 수정, 삭제한 기억 | `app/memory.py` `update_memories` |
| 대화 요약 | `memory-summary` | generation | 요약에 합친 메시지 수, 새 요약 | `app/memory.py` `maybe_summarize` |
| 먼저 말 걸기 | `greeting` | 트레이스 | 인사말 | `app/agent.py` `greet` |

## 종류를 그렇게 정한 근거

- `safety`: `is_crisis` 는 위기 표현 목록과 비교하는 코드 규칙이다. 모델을 부르지 않는다.
- `choose-tools`: `llm.can_reason` 이면 `model_choose` 가 `llm.complete_json` 을 부른다. 가짜 LLM이거나 모델 응답 파싱에 실패하면 `rule_choose` 로 넘어가 모델을 부르지 않고 토큰이 0으로 남는다. 그래서 `decided_by` 를 반드시 함께 남긴다.
- `tool:*`: `run_tool` 은 바깥 서비스를 부르고 모델은 부르지 않는다.
- `reply`: `llm.chat_stream` 을 부른다. 스트리밍이므로 사용자가 기다린 시간은 첫 글자가 나온 시각까지다.
- `memory-update`: `llm.complete_json` 을 부른다.
- `memory-summary`: 요약되지 않은 메시지가 `SUMMARIZE_AFTER = 40` 을 넘을 때만 `llm.complete_text` 를 부른다.
- `greeting`: `llm.complete_text` 를 부른다. `/api/greeting` 이라는 다른 요청이므로 트레이스를 따로 만든다.

## 주의

- `run_turn` 은 제너레이터다. 이벤트를 하나씩 yield 하며 기억 정리까지 같은 요청 안에서 끝난다. 계측이 제너레이터를 다 소비한 뒤에 트레이스를 닫아야 `memory-update` 가 트레이스 안에 들어간다.
- 기억 정리는 사용자가 답을 다 받은 뒤에 일어나지만 비용은 똑같이 든다.

## 트레이스 속성

| 속성 | 값 |
|---|---|
| `user_id` | 로그인으로 확인한 `uid` |
| `session_id` | `uid` + 한국 날짜 (하루치 대화를 한 세션으로 본다) |
| `environment` | `ENV` 환경변수 (local, staging, production) |
| `version` | 배포 버전 |

## 개인정보

대화와 기억은 개인정보다(AGENTS.md). 원문이 없으면 답이 왜 이상했는지 읽을 수 없으므로, Langfuse 에는 원문을 남기되 보내기 전에 마스킹한다.

- 가릴 패턴: 전화번호, 이메일, 카드 번호, 주민등록번호
- 서버 로그에는 원문을 남기지 않는다. 이 규칙은 그대로 둔다.
- 볼 수 있는 사람: Langfuse 프로젝트 멤버
- 보관 기간: 무료 플랜 30일
