# Discord Support Agent (서버 운영 지원 봇) 프로젝트 기획서

## 1. 프로젝트 개요

**Discord Support Agent**는 Discord 서버에 설치되어 관리자가 설정한 기능(Skill/MCP)을 토대로 다양한 잡무를 자동 수행하는 **서버 전용 AI 에이전트**입니다. 기존 Slack 기반 모니터 봇(`slack-monitor-share`)의 아이디어를 바탕으로, Discord 플랫폼에 최적화된 형태로 재설계되었습니다.

- **Server Install 전용**: 특정 서버(Guild)에 봇으로 설치되어, 해당 서버 내 모든 메시지와 이벤트를 감지할 수 있습니다. (User Install 미사용)
- **Ollama 기반 AI**: 별도의 클라우드 API 비용 없이, Ollama 서버에 접근하여 LLM을 활용합니다. (로컬 또는 원격 Ollama 서버 지원)
- **자동 호출 감지**: 슬래시 명령어나 멘션 외에도, 대화 맥락을 분석하여 봇을 호출했다고 판단되면 **자동으로 반응**합니다.
- **Skill/MCP 기반 확장**: 개인 비서가 아닌, 서버 관리자가 Skill이나 MCP를 설정하면 해당 기능을 사용하여 서버 전체를 위한 잡무를 대행합니다.

---

## 2. 핵심 아키텍처 및 기술 스택

* **언어**: Python 3.10+
* **Discord 연동 프레임워크**: `discord.py`
  * Discord Gateway(WebSocket)를 통해 서버 내 모든 메시지 이벤트를 수신합니다.
  * `MESSAGE_CONTENT` Privileged Intent를 활성화하여 메시지 본문에 접근합니다.
* **AI 엔진 연동**: `ollama` (Python 클라이언트)
  * Ollama 서버(로컬 또는 원격)에 HTTP API로 접근하여 LLM(예: `llama3`, `qwen2` 등)을 호출합니다.
  * `OLLAMA_HOST` 환경변수로 서버 주소를 지정할 수 있습니다.
* **배포 형태**: Server Install (서버 설치형 봇)
  * 특정 서버(Guild)에 봇으로 초대되어 동작합니다.
  * 서버 내 채널의 메시지를 읽고, 반응하고, 작업을 수행합니다.
* **확장 시스템**: Skill / MCP (Model Context Protocol)
  * 서버 관리자가 설정 파일을 통해 봇의 기능을 정의합니다.
  * 각 Skill은 독립적인 작업 단위(요약, 번역, 코드 리뷰, 일정 관리 등)를 나타냅니다.
  * MCP를 통해 외부 도구와 연동할 수 있습니다 (파일 시스템, DB, API 등).

---

## 3. 호출 방식

봇은 3가지 방식으로 호출됩니다.

### 방식 1: 명시적 호출 (슬래시 명령어)
* `/ask [질문]` — AI에게 자유 질의
* `/skill [스킬명] [인자]` — 특정 스킬 실행
* `/config` — 서버 관리자용 설정 명령어

### 방식 2: 멘션 호출 (@봇)
* 봇을 직접 멘션하면 메시지 내용을 분석하여 적절한 Skill로 라우팅합니다.

### 방식 3: 자동 감지 (Auto-Detect)
* 서버 내 대화를 실시간으로 모니터링합니다.
* Ollama를 사용하여 대화 맥락을 분석하고, 봇의 도움이 필요한 상황(봇 이름 언급, 도움 요청, 질문 패턴 등)을 감지합니다.
* 감지 시 해당 채널에 자동으로 반응합니다.
* **성능 고려**: 모든 메시지를 AI로 분석하면 부하가 크므로, 키워드 기반 1차 필터링 후 AI 판단을 수행합니다.
  * 1차: 봇 이름/별명/키워드 포함 여부 (정규표현식)
  * 2차: 1차 통과 시 Ollama로 맥락 분석 → 반응 여부 결정

---

## 4. Skill 시스템 (OpenClaw AgentSkills 방식)

[OpenClaw](https://openclaw.ai/)의 **[AgentSkills](https://agentskills.io)** 표준에서 영감을 받은 스킬 시스템입니다. 스킬은 Python 코드가 아닌, **`SKILL.md` 마크다운 파일**로 선언적으로 정의됩니다. 각 스킬은 AI 에이전트에게 "이런 상황에서 이렇게 행동하라"는 **플레이북(지시문)**을 제공하며, 에이전트는 이를 읽고 적절한 도구(MCP 등)를 조합하여 작업을 수행합니다.

### 4.1 핵심 개념

| OpenClaw 원본 | Discord Support Agent 적용 |
| :--- | :--- |
| 로컬 머신에서 실행, 개인 비서 | Discord 서버(Guild) 내에서 실행, 서버 지원 에이전트 |
| 여러 채팅앱(WhatsApp, Slack 등) 연동 | Discord 전용 (서버 내 채널 기반) |
| `~/.openclaw/skills` 글로벌 스킬 | `skills/` (전역) + `config/guilds/{id}/skills/` (서버별) |
| 사용자 슬래시 명령어로 실행 | Discord 슬래시 명령어 + 멘션 + **자동 감지**로 실행 |
| Tool dispatch (CLI 도구 직접 실행) | MCP 서버를 통한 외부 도구 호출 |
| `skills.entries.<name>` 로 스킬별 설정 | `config/default.yaml` + 서버별 config 에서 스킬 오버라이드 |
| `metadata.openclaw.requires` 게이팅 | 동일 구조로 스킬 로드 시 자동 필터링 |

### 4.2 SKILL.md 형식

각 스킬은 독립 디렉토리로 관리되며, `SKILL.md` 파일이 스킬의 정의입니다.

```
skills/                          # 전역 스킬 (모든 서버 공통)
├── summarize/
│   └── SKILL.md
├── translate/
│   └── SKILL.md
├── code-review/
│   ├── SKILL.md
│   └── prompts/                 # 부가 리소스 (선택)
│       └── review_template.txt
├── reminder/
│   └── SKILL.md
└── web-search/
    ├── SKILL.md
    └── scripts/                 # 보조 스크립트 (선택)
        └── parse_results.py
```

#### SKILL.md 구조 (YAML frontmatter + Markdown)

> **참고**: OpenClaw AgentSkills 파서와의 호환성을 위해 frontmatter는 **단일 라인 키**만 지원합니다. `metadata`는 **단일 라인 JSON 객체**로 작성합니다.

```markdown
---
name: summarize
description: "채널 대화나 특정 메시지를 요약합니다. 사용자가 요약을 요청하거나, 긴 대화가 이어질 때 자동으로 활성화됩니다."
triggers:
  - "요약"
  - "정리해"
  - "summarize"
  - "tldr"
user-invocable: true
metadata: { "requires": { "env": ["OLLAMA_HOST"] } }
---

# 메시지 요약 스킬

## 트리거 조건
- 사용자가 "요약해줘", "정리해줘" 등의 키워드를 사용할 때
- `/summarize` 슬래시 명령어가 호출될 때

## 실행 절차
1. 대상 메시지 또는 최근 N개 메시지를 수집한다.
2. 다음 형식으로 요약을 생성한다:
   - 핵심 주제 (1줄)
   - 주요 논의 사항 (불릿 포인트)
   - 결론 또는 액션 아이템

## 출력 형식
요약 결과를 해당 채널에 임베드(Embed) 메시지로 출력한다.

## 참고
- {baseDir}의 prompts/ 폴더에 추가 프롬프트 템플릿을 배치할 수 있다.
```

#### Frontmatter 주요 필드

| 필드 | 필수 | 기본값 | 설명 |
| :--- | :--- | :--- | :--- |
| `name` | ✅ | — | 스킬 식별자 (소문자, 하이픈 구분). 디렉토리명과 일치해야 함 |
| `description` | ✅ | — | 스킬 설명. AI가 이 설명을 읽고 적절한 스킬을 선택함 |
| `triggers` | ❌ | `[]` | 자동 감지용 키워드 목록. 1차 필터에 사용 (Discord 전용 확장) |
| `user-invocable` | ❌ | `true` | `true`이면 Discord 슬래시 명령어로 노출 |
| `disable-model-invocation` | ❌ | `false` | `true`이면 AI 프롬프트에서 제외 (슬래시 명령어로만 실행 가능) |
| `command-dispatch` | ❌ | — | `tool` 설정 시 슬래시 명령어가 AI를 거치지 않고 직접 도구 실행 |
| `command-tool` | ❌ | — | `command-dispatch: tool` 시 호출할 도구(MCP 도구명) |
| `command-arg-mode` | ❌ | `raw` | 도구 디스패치 시 인자 전달 방식 (`raw`: 원시 문자열 전달) |
| `metadata` | ❌ | `{}` | 단일 라인 JSON. 게이팅·환경변수·MCP 요구사항 등 포함 |

#### metadata 주요 키 (Gating — 로드 타임 필터)

스킬 로드 시점에 아래 조건을 검사하여, 충족하지 않는 스킬은 **자동 비활성화**됩니다.

| 키 | 설명 |
| :--- | :--- |
| `requires.env` | 필요한 환경변수 목록. 해당 환경변수가 존재하거나 config에서 제공되어야 함 |
| `requires.bins` | PATH에 존재해야 하는 바이너리 목록 (모두 충족) |
| `requires.anyBins` | PATH에 존재해야 하는 바이너리 목록 (하나 이상 충족) |
| `requires.mcp` | 필요한 MCP 서버 목록 |
| `requires.config` | 설정 파일에서 truthy여야 하는 값 경로 목록 |

### 4.3 스킬 로딩 우선순위

```
1. 서버별 스킬 (최고 우선순위):  config/guilds/{guild_id}/skills/
2. 전역 스킬:                   skills/
3. 번들 스킬 (기본 내장):        bundled_skills/
```

동일한 이름의 스킬이 여러 위치에 있으면 **상위 우선순위가 덮어씁니다** (서버별 > 전역 > 번들). 이를 통해 서버 관리자가 기본 스킬을 커스터마이징할 수 있습니다.

### 4.4 에이전트 동작 루프

OpenClaw의 **Think → Plan → Act → Observe → Repeat** 루프를 참고합니다.

```
메시지 수신
    ↓
[1] 호출 판단: 멘션? 슬래시 명령어? 키워드 매칭? → AI 맥락 분석?
    ↓
[2] 스킬 라우팅: 로드된 SKILL.md들의 description을 참고하여 적절한 스킬 선택
    ↓
[3] 실행: 선택된 스킬의 지시문(Markdown body)에 따라 작업 수행
    │     필요 시 MCP 도구 호출, 채널 메시지 수집 등
    ↓
[4] 결과 출력: Discord 채널에 응답 (Embed, 텍스트, 버튼 UI 등)
```

### 4.5 MCP 연동

MCP(Model Context Protocol)를 통해 외부 도구를 에이전트에 연결합니다. 스킬은 `metadata.requires.mcp`로 필요한 MCP 서버를 선언하고, 에이전트가 실행 시 해당 도구를 가져다 씁니다.

```yaml
# config/mcp_servers.yaml
servers:
  filesystem:
    command: "npx"
    args: ["-y", "@modelcontextprotocol/server-filesystem", "/data"]
  database:
    command: "npx"
    args: ["-y", "@modelcontextprotocol/server-sqlite", "db.sqlite"]
  web-search:
    command: "python"
    args: ["mcp_servers/web_search.py"]
```

### 4.6 서버별 설정 (디폴트 + 오버라이드)

**모든 서버는 `config/default.yaml`의 디폴트 설정을 기본으로 사용**하고, 서버별 설정 파일은 **차이점만 기술하여 deep merge**합니다. 이를 통해 새 서버 추가 시 설정 없이도 바로 동작하며, 필요한 부분만 오버라이드할 수 있습니다.

#### 설정 로딩 순서

```
1. config/default.yaml           ← 전체 기본값 (항상 로드)
2. config/guilds/{guild_id}/config.yaml  ← 서버별 오버라이드 (있으면 deep merge)
```

#### 디폴트 설정 파일

```yaml
# config/default.yaml — 모든 서버에 공통 적용되는 기본값
skills:
  enabled: []                     # 빈 리스트 = 전체 활성화
  disabled: []                    # 명시적 비활성화 목록
  entries: {}                     # 개별 스킬 오버라이드 (OpenClaw 호환)

auto_detect:
  enabled: true
  channels: []                    # 빈 리스트 = 전체 채널 감시
  keywords: []                    # 추가 감지 키워드 (봇 이름은 기본 포함)

mcp_servers: []                   # 사용 가능한 MCP 서버 목록

ollama:
  model: "llama3"                 # 기본 LLM 모델
  host: null                      # null이면 OLLAMA_HOST 환경변수 사용

response:
  language: "auto"                # 응답 언어 (auto = 요청 언어 따라감)
  max_length: 2000                # Discord 메시지 길이 제한
  use_embed: true                 # Embed 형식 사용 여부
```

#### 서버별 오버라이드 예시

서버별 config는 **변경이 필요한 부분만** 작성합니다. 명시하지 않은 항목은 `default.yaml` 값을 그대로 사용합니다.

```yaml
# config/guilds/123456789/config.yaml — 차이점만 기술
guild_name: "My Server"           # 관리용 레이블 (선택)

skills:
  disabled:
    - code-review                 # 이 서버에서는 코드 리뷰 비활성화
  entries:
    summarize:
      config:
        default_count: 50         # 요약 시 기본 메시지 수를 50으로 변경
    translate:
      config:
        target_lang: "en"         # 이 서버에서는 번역 대상을 영어로 고정

auto_detect:
  channels:
    - general
    - dev-chat                    # 이 서버에서는 특정 채널만 감시

ollama:
  model: "qwen2"                  # 이 서버에서는 다른 모델 사용
```

#### 병합 규칙

| 타입 | 병합 방식 | 예시 |
| :--- | :--- | :--- |
| dict (객체) | **deep merge** — 키 단위로 재귀 병합 | `ollama.model` 만 오버라이드 가능 |
| list (배열) | **replace** — 서버 값이 있으면 통째로 교체 | `auto_detect.channels` 지정 시 디폴트 대체 |
| scalar (값) | **replace** — 서버 값이 있으면 교체 | `ollama.model: "qwen2"` |

---

## 5. 기존 Slack 봇과의 차이점

| 구분 | 기존 Slack 모니터 봇 | Discord Support Agent |
| :--- | :--- | :--- |
| **설치 범위** | 워크스페이스 단위 | 서버(Guild) 단위 (Server Install) |
| **메시지 감지** | Socket Mode로 수동적 모니터링 | Gateway + `MESSAGE_CONTENT` Intent로 서버 내 전체 메시지 감지 |
| **호출 방식** | 멘션만 감지 | 멘션 + 슬래시 명령어 + **자동 감지** (대화 맥락 분석) |
| **AI 연동** | `claude` CLI를 `subprocess`로 실행 | Ollama 서버 HTTP API 호출 (가볍고 안정적) |
| **역할** | 개인 비서 (DM으로 알림) | **서버 전체 지원 에이전트** (채널 내 직접 반응) |
| **확장성** | 하드코딩된 기능 | **Skill/MCP 기반** 플러그인 아키텍처 |

---

## 6. 초기 구현 단계 (Next Steps)

1. **디스코드 개발자 포털 설정**
   * 새로운 Application 생성.
   * Bot 섹션에서 `MESSAGE_CONTENT` Privileged Intent 활성화.
   * OAuth2 URL 생성 시 `bot` scope + 필요 권한(메시지 읽기/쓰기, 슬래시 명령어) 설정.
   * **Guild Install만 활성화**, User Install은 비활성화.

2. **Ollama 서버 구축**
   * Ollama 설치 및 사용할 모델 다운로드 (`ollama pull llama3`).
   * 원격 접근이 필요한 경우 `OLLAMA_HOST` 설정.

3. **기본 뼈대 코드 작성 (PoC)**
   * `discord.py` 봇 로그인 및 Gateway 이벤트 수신 확인.
   * `on_message` 이벤트 핸들러로 서버 내 메시지 수신 테스트.
   * 간단한 `/ping` 슬래시 명령어 통신 테스트.

4. **자동 감지 시스템 구현**
   * 키워드 기반 1차 필터 구현.
   * Ollama 연동하여 맥락 분석 2차 판단기 구현.
   * 멘션 + 자동 감지 통합 라우팅.

5. **Skill 시스템 구현**
   * Skill 베이스 클래스 및 레지스트리 구현.
   * 기본 스킬 (요약, 번역, 자유 질의) 구현.
   * 슬래시 명령어 ↔ Skill 연동.

6. **MCP 연동**
   * MCP 클라이언트 구현.
   * 설정 파일 기반 MCP 서버 관리.

7. **서버별 설정 시스템**
   * Guild별 설정 파일 로드/저장.
   * `/config` 관리자 명령어 구현.
