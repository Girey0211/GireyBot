---
name: docker-service
description: "Docker 컨테이너를 관리합니다. 컨테이너 시작, 종료, 상태 확인, 로그 조회 요청 시 활성화됩니다."
triggers:
  - "도커"
  - "docker"
  - "컨테이너"
  - "container"
user-invocable: true
executor: docker
metadata: {}
---

# Docker 서비스 관리 스킬

## 트리거 조건

- "도커 컨테이너 목록", "docker 상태" 등 Docker 관련 요청
- `/skill docker-service <command>` 슬래시 명령어

## 의도 분류

사용자 메시지에서 아래 의도를 파악한다:

| 의도 | 키워드 예시 |
| ------- | ------------ |
| `list` | 목록, 리스트, 뭐가 돌아가고 있어, ps, list |
| `start` | 켜, 시작, 실행, 올려, start, up |
| `stop` | 꺼, 종료, 내려, stop, down |
| `restart` | 재시작, 리스타트, restart |
| `logs` | 로그, 에러, 뭐라고 뜨는지, logs |
| `status` | 상태, 확인, status |

의도를 파악할 수 없으면 `list`를 기본으로 실행한다.

## 실행 절차

### list (컨테이너 목록)

1. `docker ps -a --format '{{.Names}}\t{{.Status}}\t{{.Ports}}'`를 실행한다.
2. 결과를 테이블 형태로 보여준다.

### start / stop / restart

1. 대상 컨테이너 이름을 파악한다.
2. 컨테이너 이름이 불명확하면 사용자에게 확인한다.
3. `docker start/stop/restart <container>`를 실행한다.
4. 실행 후 상태를 확인하여 결과를 보고한다.

### logs (로그 조회)

1. 대상 컨테이너 이름을 파악한다.
2. `docker logs --tail 30 <container>`로 최근 로그를 조회한다.
3. 로그를 코드 블록으로 출력한다.

### status (상태 확인)

1. `docker inspect --format '{{.State.Status}}' <container>`를 실행한다.
2. 상태에 따라 이모지로 표시:
   - 🟢 running
   - 🔴 exited / dead
   - 🟡 paused / restarting

## 출력 형식

Embed 메시지로 응답한다:

- **제목**: 🐳 Docker 서비스 관리
- **색상**: 실행=초록, 종료=빨강, 상태 조회=파랑

## 에러 처리

- Docker 미설치 → "Docker가 설치되어 있지 않습니다."
- 컨테이너 미발견 → "해당 이름의 컨테이너를 찾을 수 없습니다."
- 권한 부족 → "Docker 실행 권한이 없습니다. sudo 설정을 확인하세요."
