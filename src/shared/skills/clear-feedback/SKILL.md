---
description: 피드백을 초기화하는 스킬
executor: local
name: clear-feedback
triggers:
- 피드백
- 피드백 초기화
- 피드백 리셋
- 피드백 지워
- 제재 초기화
- 점수 초기화
- feedback
- reset
user-invocable: true
---

# clear-feedback

## 트리거 조건

- 사용자가 관련 키워드를 포함한 메시지를 보낼 때 활성화됩니다.
- `/skill clear-feedback` 슬래시 명령어로 직접 실행할 수 있습니다.

## 의도 분류

사용자 메시지에서 아래 의도를 파악한다:

| 의도 | 키워드 예시 |
| ------ | ------------ |
| `status` | 피드백 초기화 |

의도를 파악할 수 없으면 사용자에게 되물어본다.

## 실행 절차

1. 아래 명령어를 command 블록으로 출력한다:

```command
sqlite3 /Users/mobyus/workplace/GireyBot/data/memory.db "UPDATE user_feedback SET score = 0;"
```

2. 실행 결과(exit code 0)를 확인하고 성공 여부를 사용자에게 알린다.

## 출력 형식

초기화 결과를 간결한 텍스트로 응답한다. JSON이나 코드 블록 없이 자연어로만 작성한다.

## 에러 처리

- 연결 실패 → "서버에 접근할 수 없습니다."
- 권한 부족 → "실행 권한이 없습니다."
