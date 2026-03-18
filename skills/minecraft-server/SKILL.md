---
name: minecraft-server
description: "마인크래프트 서버를 원격으로 관리합니다. 서버 시작, 종료, 상태 확인 요청 시 활성화됩니다."
triggers:
  - "마크서버"
  - "마크 서버"
  - "마인크래프트"
  - "minecraft"
  - "서버 켜"
  - "서버 꺼"
user-invocable: true
executor: ssh
credentials: minecraft-server.yaml
---

# 마인크래프트 서버 관리 스킬

## 트리거 조건

- "마크서버 켜줘", "마크 서버 꺼", "마크서버 상태" 등 마인크래프트 서버 관련 요청
- `/skill minecraft-server start|stop|status` 슬래시 명령어

## 의도 분류

사용자 메시지에서 아래 의도를 파악한다:

| 의도 | 키워드 예시 |
| ------ | ------------ |
| `start` | 켜, 시작, 실행, 올려, start, on |
| `stop` | 꺼, 종료, 내려, 멈춰, stop, off |
| `status` | 상태, 확인, 돌아가고 있어?, 살아있어?, status |

의도를 파악할 수 없으면 사용자에게 되물어본다.

## 실행 절차

### status (상태 확인)

1. SSH로 대상 호스트에 접속한다.
2. `systemctl is-active minecraft.service` 또는 프로세스 확인 명령을 실행한다.
3. 결과를 사용자에게 보고한다:
   - 🟢 실행 중 (포트, 업타임 포함)
   - 🔴 중지됨

### start (서버 시작)

1. 먼저 `status`를 확인한다.
2. 이미 실행 중이면 "이미 실행 중입니다"라고 알린다.
3. 중지 상태면 `sudo systemctl start minecraft.service`를 실행한다.
4. 5초 대기 후 다시 상태를 확인하여 성공 여부를 보고한다.

### stop (서버 종료)

1. 먼저 `status`를 확인한다.
2. 이미 중지 상태면 "이미 꺼져 있습니다"라고 알린다.
3. 실행 중이면 `sudo systemctl stop minecraft.service`를 실행한다.
4. 종료 확인 후 결과를 보고한다.

## 출력 형식

Embed 메시지로 응답한다:

- **제목**: 🎮 마인크래프트 서버
- **색상**: 시작 성공=초록, 종료 성공=빨강, 상태 조회=파랑, 에러=노랑
- **필드**: 상태, 호스트, 실행 결과

## 에러 처리

- SSH 연결 실패 → "서버에 접근할 수 없습니다. 관리자에게 문의하세요."
- 권한 부족 → "서버 제어 권한이 없습니다."
- 타임아웃 → "서버 응답이 없습니다. 잠시 후 다시 시도하세요."

## 권한

이 스킬은 서버 인프라를 직접 제어하므로, 서버별 config에서
`skills.entries.minecraft-server.allowed_roles`에 지정된 역할을 가진 사용자만 실행할 수 있다.
