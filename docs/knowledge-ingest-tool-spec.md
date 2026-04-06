# Knowledge Ingest Tool — 기능 명세서

GireyBot의 RAG 지식베이스에 데이터를 삽입하기 위한 독립 CLI 도구.
봇과 동일한 SQLite DB + ChromaDB에 연결하여 외부에서 문서를 관리한다.

---

## 개요

| 항목 | 내용 |
|------|------|
| 언어 | Python 3.12+ |
| 실행 방식 | CLI (uv run / python -m) |
| 연결 대상 | GireyBot SQLite (`data/bot.db`) + ChromaDB (Docker) |
| 설정 파일 | 봇과 동일한 `secrets.yaml` 참조 |

---

## 기능 목록

### 1. URL 인제스트 (`ingest url`)

웹 페이지 URL을 여러 개 받아 본문을 추출하고 지식베이스에 저장한다.

```
uv run ingest url \
  --urls "https://namu.wiki/w/파우스트" "https://namu.wiki/w/파우스트/작중행적" \
  --category people \
  [--title "파우스트"]
```

- URL 여러 개를 한 번에 처리
- 각 URL → 별도 `knowledge_doc` (제목은 페이지 `<title>` 자동 추출, `--title` 지정 시 앞에 prefix로 붙임)
- HTML 본문 추출: `trafilatura` 사용
- 성공/실패 결과를 터미널에 출력

---

### 2. 파일 인제스트 (`ingest file`)

로컬 파일(`.md`, `.txt`, `.pdf`)을 지식베이스에 저장한다.

```
uv run ingest file \
  --path "docs/faust.md" \
  --title "파우스트 정보" \
  --category people
```

- 단일 파일 또는 디렉토리 전체 처리 (`--path` 가 디렉토리이면 재귀 탐색)
- PDF는 `pypdf` 로 텍스트 추출

---

### 3. 목록 조회 (`ingest list`)

저장된 문서 목록을 출력한다.

```
uv run ingest list [--category people]
```

출력 형식:
```
ID  제목                    카테고리   청크수   저장일
1   파우스트                 people     4        2026-04-01
2   파우스트/작중행적         people     12       2026-04-01
```

---

### 4. 문서 삭제 (`ingest delete`)

ID로 문서를 SQLite + ChromaDB에서 동시 삭제한다.

```
uv run ingest delete --id 1
uv run ingest delete --id 1 2 3   # 여러 개 동시 삭제
```

---

### 5. 재인덱싱 (`ingest reindex`)

SQLite의 모든 문서를 ChromaDB에 다시 인덱싱한다. (ChromaDB 초기화 후 복구 용도)

```
uv run ingest reindex
```

---

## 공통 옵션

| 옵션 | 설명 | 기본값 |
|------|------|--------|
| `--config` | secrets.yaml 경로 | `secrets.yaml` |
| `--db` | SQLite DB 경로 | `data/bot.db` |
| `--dry-run` | 실제 저장 없이 처리 결과만 출력 | false |
| `--verbose` | 상세 로그 출력 | false |

---

## 카테고리

| 값 | 설명 |
|----|------|
| `people` | 인물 정보 |
| `rules` | 서버 규칙 |
| `events` | 이벤트 |
| `general` | 일반 (기본값) |

---

## 프로젝트 위치 결정

**GireyBot 모노레포 내 포함** (`tools/ingest/`)

별도 프로젝트로 분리하지 않는다.

**이유:**
- `src/main/rag/` (ingest, store, embedder), `src/main/memory/manager.py` 코드를 그대로 import 가능 — 중복 없음
- `secrets.yaml`, `data/bot.db` 경로를 그대로 공유
- 별도 분리 시 RAG 코드 변경 때마다 두 프로젝트를 동기화해야 하는 부담 발생
- `trafilatura` 등 추가 의존성은 봇 런타임에 영향 없음 (import 하지 않으면 로드 안 됨)

---

## 프로젝트 구조

GireyBot 레포 내에 `tools/ingest/` 디렉토리로 추가한다.

```
GireyBot/
├── src/main/rag/          # 봇 코드 — 그대로 재사용
│   ├── ingest.py
│   ├── store.py
│   └── embedder.py
├── src/main/memory/
│   └── manager.py
├── tools/
│   └── ingest/
│       ├── __main__.py        # CLI 진입점: python -m tools.ingest <command>
│       ├── fetcher.py         # URL fetch + trafilatura 본문 추출
│       └── commands/
│           ├── url.py         # ingest url
│           ├── file.py        # ingest file
│           ├── list.py        # ingest list
│           ├── delete.py      # ingest delete
│           └── reindex.py     # ingest reindex
├── pyproject.toml             # trafilatura 의존성 추가
└── secrets.yaml
```

**실행:**
```bash
python -m tools.ingest url --urls "https://..." --category people
```

---

## 미결 사항

없음
