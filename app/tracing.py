"""Langfuse 계측을 한곳에 모아 둔다.

키(LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY)가 없으면 아무것도 보내지 않고 앱은 그대로 돈다.
관찰 단위 이름은 docs/langfuse-plan.md 의 표를 그대로 쓴다.

대화 원문은 남긴다. 서버 로그에는 원문을 남기지 않는 규칙(AGENTS.md)은 그대로 두고,
접근 권한이 걸린 Langfuse 에만 남기되 보내기 전에 아래 패턴을 가린다.
"""
import os
import re
from contextlib import contextmanager, nullcontext

from app import when
from app.logs import log

# 보내기 전에 가릴 개인정보 패턴. 자릿수가 긴 것부터 본다.
MASKS = [
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]{2,}"), "[이메일]"),
    (re.compile(r"\d{6}[-\s]?[1-4]\d{6}"), "[주민등록번호]"),
    (re.compile(r"\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}"), "[카드번호]"),
    (re.compile(r"01\d[-\s.]?\d{3,4}[-\s.]?\d{4}"), "[전화번호]"),
]

_client = None
_looked = False


def mask(*, data, **kwargs):
    """Langfuse 로 보내기 전에 값에서 개인정보 패턴을 가린다. SDK가 입력과 출력마다 부른다."""
    if isinstance(data, str):
        for pattern, label in MASKS:
            data = pattern.sub(label, data)
        return data
    if isinstance(data, dict):
        return {key: mask(data=value) for key, value in data.items()}
    if isinstance(data, list):
        return [mask(data=item) for item in data]
    return data


def client():
    """키가 있을 때만 클라이언트를 만든다. 없으면 None 이고 계측은 꺼진다.

    .env 를 읽는 load_settings() 뒤에 불려야 해서 가져올 때가 아니라 쓸 때 만든다.
    """
    global _client, _looked
    if _looked:
        return _client
    _looked = True
    if not (os.environ.get("LANGFUSE_PUBLIC_KEY") and os.environ.get("LANGFUSE_SECRET_KEY")):
        log("INFO", "Langfuse 키가 없어 계측을 끕니다")
        return None
    try:
        from langfuse import Langfuse

        _client = Langfuse(mask=mask)
        log("INFO", "Langfuse 계측을 켭니다")
    except Exception as error:  # 계측 때문에 서비스가 멈추지 않게 한다
        log("WARNING", "Langfuse 시작 실패, 계측을 끕니다", error=type(error).__name__)
    return _client


def session_id(uid):
    """하루치 대화를 한 세션으로 본다. 서비스에 대화방이 없어서 기준을 직접 정한다."""
    return f"{uid}-{when.now_in('Asia/Seoul'):%Y-%m-%d}"


def deployment():
    """어느 환경의 어느 배포가 만든 기록인지. K_REVISION 은 Cloud Run 이 넣어 준다."""
    return {"environment": os.environ.get("ENV", "local").strip() or "local",
            "version": os.environ.get("K_REVISION", "local")}


def commit():
    return os.environ.get("COMMIT_SHA", "local").strip() or "local"


def attributes(**fields):
    """이 블록 안에서 만들어지는 관찰 단위에 속성을 함께 붙인다.

    태그는 관찰 단위를 만들 때만 붙는다. 나중에 update(tags=...) 로는 붙지 않는다.
    """
    lf = client()
    if lf is None:
        return nullcontext()
    from langfuse import propagate_attributes

    return propagate_attributes(**fields)


def _in_span():
    """지금 열려 있는 관찰 단위가 있는지. 없을 때 SDK 함수를 부르면 오류 줄이 찍힌다.

    scripts/eval_tools.py 처럼 app/llm.py 를 직접 부르는 코드는 트레이스 밖에서 돈다.
    """
    from opentelemetry import trace as otel_trace

    return otel_trace.get_current_span().get_span_context().is_valid


def prompt(name, label=None):
    """Langfuse 에 둔 프롬프트를 가져온다. 못 가져오면 None 이고, 부른 쪽이 코드의 기본 문장을 쓴다.

    라벨은 ENV 로 정한다. production 환경만 production 라벨을 보고, 나머지는 staging 라벨을 본다.
    배포는 라벨을 옮기는 일이고 롤백도 같다. 코드를 다시 배포하지 않는다.
    """
    lf = client()
    if lf is None:
        return None
    label = label or ("production" if os.environ.get("ENV", "local").strip() == "production" else "staging")
    try:
        return lf.get_prompt(name, label=label)
    except Exception as error:  # Langfuse 에 닿지 못해도 서비스는 답해야 한다
        log("WARNING", "Langfuse 프롬프트를 가져오지 못해 코드의 기본 문장을 씁니다",
            name=name, label=label, error=type(error).__name__)
        return None


def current_trace_id():
    """지금 열려 있는 트레이스의 ID. 반응을 점수로 붙일 때 쓰려고 메시지에 함께 저장한다."""
    lf = client()
    return lf.get_current_trace_id() if lf is not None else None


def score(trace_id, name, value, comment=None):
    """사용자 반응을 트레이스에 점수로 붙인다. 계측이 꺼져 있거나 트레이스 ID가 없으면 건너뛴다."""
    lf = client()
    if lf is None or not trace_id:
        return False
    try:
        lf.create_score(name=name, value=value, trace_id=trace_id, data_type="BOOLEAN",
                        comment=mask(data=comment) if comment else None)
        lf.flush()
        return True
    except Exception as error:  # 점수 때문에 사용자 요청이 실패하지 않게 한다
        log("WARNING", "Langfuse 점수 전송 실패", error=type(error).__name__)
        return False


def flush():
    """버퍼에 쌓인 기록을 지금 보낸다.

    Cloud Run 은 요청을 처리하는 동안에만 CPU를 넉넉히 줘서, 응답이 끝난 뒤에는
    백그라운드 스레드가 못 보내고 기록이 사라질 수 있다. 대가는 응답이 아주 조금 늦어지는 것.
    """
    lf = client()
    if lf is not None:
        lf.flush()


@contextmanager
def observe(name, as_type="span", **fields):
    """관찰 단위 하나를 연다. 계측이 꺼져 있으면 None 을 돌려주고 아무것도 보내지 않는다."""
    lf = client()
    if lf is None:
        yield None
        return
    with lf.start_as_current_observation(name=name, as_type=as_type, **fields) as observation:
        yield observation


def update(observation, **fields):
    if observation is not None:
        observation.update(**fields)


def follow(iterator):
    """제너레이터의 각 단계를 트레이스가 열린 컨텍스트에서 실행한다.

    StreamingResponse 는 동기 제너레이터를 워커 스레드 풀에서 한 조각씩 돌려서,
    next() 마다 스레드가 바뀔 수 있다. 지금 열려 있는 관찰 단위는 컨텍스트 변수로 따라가므로
    그대로 두면 yield 뒤의 단계가 부모를 잃고 따로 트레이스가 된다.
    조각마다 같은 컨텍스트 안에서 돌려서, 관찰 단위를 열고 닫는 일이 모두 한 컨텍스트에서 일어나게 한다.
    """
    if client() is None:
        yield from iterator
        return
    import contextvars

    ctx = contextvars.copy_context()
    try:
        while True:
            try:
                item = ctx.run(next, iterator)
            except StopIteration:
                return
            yield item
    finally:
        ctx.run(iterator.close)  # 사용자가 중간에 끊어도 관찰 단위를 같은 컨텍스트에서 닫는다


def record_usage(model, input_tokens, output_tokens):
    """모델을 부른 뒤 지금 열려 있는 generation 에 모델 이름과 토큰 수를 남긴다.

    app/llm.py 의 세 클래스가 모두 이 함수를 불러서, 제공자를 바꿔도 기록이 빠지지 않는다.
    """
    lf = client()
    if lf is None or not _in_span():
        return
    try:
        lf.update_current_generation(
            model=model,
            usage_details={"input": int(input_tokens or 0), "output": int(output_tokens or 0)},
        )
    except Exception as error:
        log("WARNING", "Langfuse 토큰 기록 실패", error=type(error).__name__)
