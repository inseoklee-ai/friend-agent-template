# 로그와 피드백으로 에이전트 개선하기

## 데이터셋과 기준 (실험 전에 적음)

- 데이터셋: `data/eval/date_reply.jsonl`, Langfuse 데이터셋 `date-reply`. 문제 12, 정상 12.
- 실행: 고치기 전(before)과 후(after)를 각각 2회.
- 내보낼 기준:
  1. after 문제 항목 통과가 2회 평균 10/12 이상.
  2. after 문제 항목 통과가 before 평균보다 4개 이상 많음.
  3. after 정상 항목 통과가 매 회 11/12 이상이고, before 평균보다 1개 넘게 떨어지지 않음.
