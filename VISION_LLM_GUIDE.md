# Vision LLM 사용법 & 코드 동작원리 가이드

## 1. 프로젝트 개요

이 프로젝트는 **FastAPI 기반 학술 논문 읽기 보조 시스템**입니다. PDF 논문을 업로드하면:

- 📄 **구조화된 추출**: 페이지별 마크다운, 테이블, 그림, 목차
- 🔍 **RAG 검색**: 텍스트 임베딩 벡터 인덱스로 빠른 검색
- 🎨 **그림 설명**: Vision LLM으로 논문의 그림/차트 해석
- 💬 **상호작용**: 단어 정의, 문장 설명, RAG 챗, 그래프 추출, Wiki 생성

### 기술 스택
- **백엔드**: FastAPI (Python)
- **PDF 처리**: PyMuPDF (fitz), pdfplumber, pymupdf4llm
- **LLM**: OpenAI API (gpt-4o, gpt-4o-mini)
- **벡터 DB**: 자체 RAG 인덱스 (numpy + JSON)
- **프런트엔드**: 브라우저 기반 웹 뷰어

---

## 2. 모델 구성 (`config.py`)

### 사용하는 세 가지 모델

```
LLM_MODEL = "gpt-4o-mini"         # 텍스트 작업 (경제적)
VISION_MODEL = "gpt-4o"           # 이미지+텍스트 (멀티모달)
EMBEDDING_MODEL = "text-embedding-3-small"  # 임베딩 (RAG용)
```

### 각 모델의 역할

| 모델 | 용도 | 예시 |
|---|---|---|
| **gpt-4o-mini** | 텍스트 기반 LLM 작업 | 단어 정의, 문장 설명, 챗, 요약, 그래프 추출 |
| **gpt-4o** | Vision 멀티모달 | 논문의 그림/차트/다이어그램 설명 |
| **text-embedding-3-small** | 검색 임베딩 | 텍스트 청크 → 벡터로 변환 (RAG 검색용) |

### 환경 변수 설정 (`env.txt`)

```env
# OpenAI API (필수)
OPENAI_API_KEY=sk-...

# 모델 선택 (기본값 사용 권장)
LLM_MODEL=gpt-4o-mini
VISION_MODEL=gpt-4o
EMBEDDING_MODEL=text-embedding-3-small

# [선택사항] 로컬 vLLM 엔드포인트로 텍스트 LLM 오프로드
# LLM_BASE_URL=http://localhost:8000/v1
# api_key는 자동으로 "EMPTY"로 설정됨

# [선택사항] Vision 모델을 다른 엔드포인트로
# VISION_BASE_URL=http://localhost:8001/v1

# [선택사항] 임베딩 모델을 다른 엔드포인트로
# EMBEDDING_BASE_URL=http://localhost:8002/v1
```

**핵심**: 각 모델을 **독립적으로 다른 백엔드로 라우팅 가능** → 예: 텍스트는 로컬 vLLM, Vision은 OpenAI API

---

## 3. Vision LLM 사용법

### 3.1 LLM 클라이언트 구조 (`llm_client.py`)

```python
# 세 가지 독립적인 LLM 클라이언트 (LangChain 래퍼)

_get_llm(model)           # 텍스트 LLM (chat 함수용)
_get_vision_llm(model)    # Vision LLM (멀티모달용)
_get_embeddings(model)    # 임베딩 모델 (RAG용)
```

각 함수는 `@functools.lru_cache` 데코레이터로 초기화되어, 같은 모델은 한 번만 생성됩니다.

### 3.2 Vision 메시지 포맷 (OpenAI 표준)

```python
messages = [
    {
        "role": "user",
        "content": [
            {
                "type": "text",
                "text": "이 그림을 설명해 주세요."
            },
            {
                "type": "image_url",
                "image_url": {
                    "url": "data:image/png;base64,iVBORw0KGgo..."
                }
            }
        ]
    }
]

# llm_client.stream_vision_messages(messages, model="gpt-4o")
# → 토큰 스트리밍 시작
```

**핵심 포인트**:
- 이미지는 **base64 data URI** 형식 (네트워크 전송 최소화)
- 텍스트와 이미지가 **같은 content 배열** 안에 동시 전달
- `stream_vision_messages()` 호출로 토큰 스트리밍

### 3.3 이미지 준비: PDF → Base64 PNG

```python
# PyMuPDF (fitz) 사용, 2x 해상도 렌더링
import fitz

zoom = 2.0  # 선명도 향상
matrix = fitz.Matrix(zoom, zoom)

# 전체 페이지 렌더링
pix = pdf_doc[page_index].get_pixmap(matrix=matrix)
png_bytes = pix.tobytes("png")

# Base64 인코딩
import base64
img_b64 = base64.b64encode(png_bytes).decode('utf-8')

# 위 메시지 포맷의 URL로 사용
url = f"data:image/png;base64,{img_b64}"
```

**왜 2x 해상도?** → 논문의 작은 텍스트, 복잡한 차트도 Vision LLM이 명확하게 인식

### 3.4 Figure 설명 두 가지 경로 (`figure_explain.py`)

#### 경로 A: paper.json 메타데이터 사용

```python
# figure_explain.stream(ctx, figure_id)
#   1. paper.json에서 figure_id 검색
#   2. 포함된 페이지 번호와 bbox 획득
#   3. 전체 페이지 렌더링 → PNG → Base64
#   4. RAG 인덱스에서 이 figure_id 언급한 텍스트 검색 (컨텍스트)
#   5. Vision LLM에 전송
#   6. 결과를 data/<paper_id>/figure_explanations/<figure_id>.md에 저장
```

**장점**: 정확한 그림 메타데이터, 빠른 처리

#### 경로 B: 텍스트 참조로 캡션 검색

```python
# figure_explain.stream_by_ref(ctx, "Figure 3")
#   1. paper.json에서 "Figure 3" 찾기 시도
#   2. 없으면 PDF 원본 스캔하여 "Figure 3" 캡션 블록 검색
#   3. 캡션 근처 영역(bbox) 추출
#   4. 그 영역만 crop 렌더링 (경로 A보다 빠름)
#   5. Vision LLM 호출
```

**장점**: 메타데이터 없이도 작동, 더 작은 이미지 전송

### 3.5 캐싱 전략

```
figure_explain.stream() / stream_by_ref()
    ↓
캐시 경로: data/<paper_id>/figure_explanations/<figure_id>.md 확인
    ↓
캐시 있음? → [캐시 반환 (LLM 호출 없음)]
캐시 없음? → [이미지 렌더링 → Vision LLM 호출 → 캐시 저장]
```

**효과**: 같은 그림 재요청 시 0.01초 응답

---

## 4. 전체 코드 동작원리

### 4.1 PDF 업로드 → 처리 파이프라인

```
사용자가 PDF 파일 업로드
    ↓
serve.py: POST /api/upload-pdf
    ↓
run.py: 전체 파이프라인 실행 (pipeline_sync.py와 동기)
    ├─ extract.py (1단계): 페이지별 마크다운 추출
    │  ├─ pymupdf4llm: 마크다운 추출 (주 방식)
    │  ├─ pdfplumber: 테이블 정제
    │  └─ PyMuPDF: 그림 추출 → data/<paper_id>/figures/*.png 저장
    │
    ├─ pipeline.py (2단계): 페이지별 요약 (gpt-4o-mini 호출, 페이지당 1회)
    │  → 각 페이지 마크다운의 2-4 문장 요약 생성
    │
    ├─ toc.py (3단계): 목차(TOC) 구성
    │  ├─ 북마크 추출 (PDF 내장)
    │  ├─ 정규식 패턴 매칭 ("# ", "## " 등)
    │  └─ 폰트 휴리스틱 (큰 텍스트 감지)
    │
    ├─ rag.py (4단계): RAG 인덱스 생성
    │  ├─ 각 페이지 마크다운을 2000자 청크로 분할 (200자 오버랩)
    │  ├─ llm_client.embed()로 임베딩 (batch=64)
    │  └─ numpy 압축 저장: data/<paper_id>/rag.npz + rag.json
    │
    └─ paper.json 저장
       (pages, figures, toc, summaries, etc.)

✓ 이제 서버에서 이 논문 사용 가능
```

### 4.2 서버 API 엔드포인트별 LLM 사용

#### 텍스트 LLM만 사용 (gpt-4o-mini)

| 엔드포인트 | 설명 | 입력 | 출력 |
|---|---|---|---|
| `POST /api/define` | 단어 정의 | `{word, before_text, after_text}` | 정의 (스트리밍) |
| `POST /api/explain` | 문장 설명 | `{sentence, before, after}` | 설명 (스트리밍) |
| `POST /api/chat` | RAG 챗봇 | `{question, page?, limit?}` | 답변 (스트리밍, 출처 인용) |
| `POST /api/highlight-all` | 강조 항목 추출 | `{paper_id}` | JSON 배열 (NDJSON 스트림) |
| `POST /api/toc-summarize-all` | 목차별 요약 | `{paper_id}` | NDJSON 요약 스트림 |

#### Vision LLM (gpt-4o) ⭐

| 엔드포인트 | 설명 |
|---|---|
| `POST /api/figure-explain` | **그림 설명 (이미지+텍스트, 멀티모달)** |

#### Wiki & 그래프 (gpt-4o-mini)

| 엔드포인트 | 설명 |
|---|---|
| `POST /api/wiki/ingest/{paper_id}` | 그래프 추출 + Wiki 페이지 생성 (NDJSON) |
| `POST /api/wiki/qa` | Wiki 기반 질의응답 (RAG+그래프) |

### 4.3 RAG (Retrieval-Augmented Generation) 동작 원리

```
사용자 질문 입력
    ↓
llm_client.embed(question)
    → 임베딩 벡터 (768차원)
    ↓
rag.py: RagIndex.search(query_vector, topk=4, page_filter?)
    → rag.npz 코사인 유사도 L2 정규화 → 상위 k개 청크 반환
    ↓
프롬프트에 삽입:
    "다음 논문 본문을 참고하여 답변하세요:
     [청크1]
     [청크2]
     [청크3]
     [청크4]
     
     Q: {question}
     A:"
    ↓
gpt-4o-mini로 답변 생성 (스트리밍)
    ↓
출처 태그 자동 추가: "[page 3]" 형식
```

**핵심**:
- 임베딩은 **사전 계산** (업로드 시 한 번만)
- 검색은 **코사인 유사도** 기반 (매우 빠름)
- RAG는 "환각(hallucination)" 방지 → **논문 내용만** 참고

### 4.4 Knowledge Graph & Wiki 생성

```
논문 텍스트 (마크다운 30KB 단위)
    ↓
graph.py: extract_concepts()
    ├─ 목차 요약을 컨텍스트로 포함
    ├─ gpt-4o-mini 호출
    ├─ 시스템 프롬프트: JSON 형식 강제
    │  {
    │    "nodes": [
    │      {"id": "concept_123", "label": "Attention", "type": "mechanism"},
    │      ...
    │    ],
    │    "edges": [
    │      {"source": "concept_123", "target": "concept_456", "relation": "enables"}
    │    ]
    │  }
    └─ 노드 7가지 타입: architecture, mechanism, concept, method, dataset, metric, person
    └─ 관계 9가지 타입: enables, generalizes, implements, motivates, ...
    ↓
merge_extraction_into_graph()
    ├─ snake_case ID로 자동 중복 제거
    ├─ 별칭 기반 node 병합
    └─ data/<paper_id>/graph.json 저장
    ↓
wiki.py: generate_page() (각 노드별 실행)
    ├─ RAG로 해당 노드의 언급 청크 topk(4) 검색
    ├─ 목차 요약에서 언급 필터링
    ├─ 1-hop 이웃 노드의 요약 포함
    ├─ gpt-4o-mini로 Wiki 페이지 작성
    │  (시스템 프롬프트: 마크다운 형식 강제, "할루시네이션 금지")
    └─ data/<paper_id>/wiki/<concept_id>.md 저장
    ↓
Wiki 페이지 서빙
    ├─ [[concept_id]] 내부 링크 → [concept_name](concept_id.md) 변환
    └─ 브라우저에서 렌더링
```

---

## 5. 핵심 파일 참고

### Python 메인 모듈

| 파일 | 역할 |
|---|---|
| `serve.py` | FastAPI 서버, 모든 엔드포인트 정의 |
| `llm_client.py` | LangChain 래퍼, 3가지 LLM 클라이언트 (캐싱) |
| `figure_explain.py` | **Vision LLM 호출, 이미지 렌더링, 캐싱** |
| `extract.py` | PDF → 마크다운/테이블/그림 추출 |
| `pipeline.py` | 페이지별 요약 생성 |
| `rag.py` | RAG 인덱스 구축 & 검색 |
| `graph.py` | 지식 그래프 추출 (gpt-4o-mini) |
| `wiki.py` | Wiki 페이지 생성 & QA (gpt-4o-mini) |
| `run.py` | 파이프라인 오케스트레이션 |

### 프롬프트 파일 (prompts/ 디렉토리)

| 파일 | 사용처 | 용도 |
|---|---|---|
| `figure_explain.system.txt` | Vision LLM | **그림 설명 (4단락 구조 강제)** |
| `figure_explain.user.txt` | Vision LLM | 템플릿: `{figure_id}`, `{page_human}`, `{caption}`, `{context}` |
| `define.system.txt` | gpt-4o-mini | 기술 용어 정의 |
| `chat.system.txt` | gpt-4o-mini | RAG 챗봇 페르소나 |
| `graph_extract.system.txt` | gpt-4o-mini | 그래프 JSON 스키마 (7 노드 타입, 9 관계 타입) |
| `wiki_page.system.txt` | gpt-4o-mini | Wiki 저자 페르소나, 마크다운 형식 강제 |
| 외 6개 | - | 다양한 보조 작업 |

---

## 6. Vision LLM 실전 팁

### 6.1 이미지 크기 최적화

```python
# 권장: 2x 해상도 (2000x2000 픽셀 범위)
zoom = 2.0

# 느린 경우: 1x로 축소
zoom = 1.0

# 매우 선명해야 하는 경우: 3x 시도 (API 지연 증가)
zoom = 3.0
```

### 6.2 캐시 무효화

그림을 다시 설명하고 싶으면:

```python
# data/<paper_id>/figure_explanations/<figure_id>.md 삭제
# → 다음 호출 시 자동으로 Vision LLM 재호출
```

### 6.3 프롬프트 튜닝

`prompts/figure_explain.system.txt` 수정 → 즉시 적용 (캐시 무효화 후)

```
현재 구조:
1. 개요 단락
2. 기술 세부사항 단락
3. 논문에서의 역할 단락
4. 주요 인사이트 단락
```

### 6.4 비용 절감

```python
# 로컬 vLLM으로 텍스트 LLM 오프로드 (대부분의 호출)
# → OpenAI API 비용 ~90% 감소
# → Vision LLM (gpt-4o)은 OpenAI API 사용
#   (필요시에만, 그림만 호출하므로 비용 최소)

# env.txt 설정:
LLM_BASE_URL=http://localhost:8000/v1
# gpt-4o-mini는 자동으로 로컬 vLLM으로 라우팅
# gpt-4o는 여전히 OpenAI API 사용
```

---

## 7. 문제 해결

### Q: Vision LLM 호출이 느려요

**A**: 
1. 이미지 해상도 낮추기 (zoom=1.0)
2. 캐시 확인: `data/<paper_id>/figure_explanations/` 있는지 확인
3. 네트워크 지연: OpenAI API 상태 확인

### Q: 그림 설명이 정확하지 않아요

**A**:
1. `prompts/figure_explain.system.txt` 수정
2. 캐시 삭제: `rm -r data/<paper_id>/figure_explanations/`
3. 재호출

### Q: 로컬 vLLM에서 Vision 모델도 쓰고 싶어요

**A**:
```python
# env.txt:
VISION_BASE_URL=http://localhost:8001/v1
VISION_MODEL=mistral-large  # 또는 ollama vision 모델
```

---

## 8. 아키텍처 요약 다이어그램

```
┌─────────────────────────────────────────────────────┐
│                 FastAPI 서버 (serve.py)             │
├─────────────────────────────────────────────────────┤
│                                                     │
│  ┌────────────────────────────────────────────┐   │
│  │  /api/chat      /api/explain               │   │
│  │  /api/define    /api/figure-explain ⭐    │   │
│  │  /api/wiki/*    /api/highlight-all        │   │
│  └────────────────────────────────────────────┘   │
│                     ↓                               │
├─────────────────────────────────────────────────────┤
│              LLM Client (llm_client.py)            │
├─────────────────────────────────────────────────────┤
│                                                     │
│  ┌──────────────────┐  ┌──────────────────┐       │
│  │ gpt-4o-mini      │  │ gpt-4o (Vision)  │⭐     │
│  │ (텍스트 LLM)     │  │ (멀티모달)       │       │
│  │ LLM_BASE_URL     │  │ VISION_BASE_URL  │       │
│  └──────────────────┘  └──────────────────┘       │
│         ↓                       ↓                   │
│    로컬 vLLM 또는         OpenAI API               │
│    OpenAI API            (또는 다른)                │
│                                                     │
│  ┌──────────────────────────────────┐             │
│  │ text-embedding-3-small           │             │
│  │ (RAG 임베딩)                     │             │
│  │ EMBEDDING_BASE_URL               │             │
│  └──────────────────────────────────┘             │
│              ↓                                      │
│       OpenAI API                                   │
│                                                     │
└─────────────────────────────────────────────────────┘
           ↓
    ┌──────────────────┐
    │  논문 데이터     │
    ├──────────────────┤
    │ paper.json       │ (메타데이터)
    │ rag.npz / .json  │ (임베딩 인덱스)
    │ figures/*.png    │ (추출된 그림)
    │ wiki/*.md        │ (생성된 Wiki)
    │ graph.json       │ (지식 그래프)
    └──────────────────┘
```

---

## 9. 핵심 요약

| 항목 | 설명 |
|---|---|
| **Vision LLM** | gpt-4o (기본), OpenAI API 또는 로컬 엔드포인트 |
| **이미지 포맷** | PDF → PyMuPDF 렌더링 → 2x 해상도 PNG → base64 data URI |
| **메시지 포맷** | OpenAI `image_url` 표준 (role+content+type+url) |
| **캐싱** | `data/<paper_id>/figure_explanations/<figure_id>.md` |
| **호출 위치** | `figure_explain.py` 의 `stream()` / `stream_by_ref()` |
| **스트리밍** | `llm_client.stream_vision_messages(messages)` |
| **비용 절감** | 텍스트 LLM만 로컬 vLLM으로 오프로드 (Vision은 그대로) |
| **텍스트 LLM** | gpt-4o-mini (기본), 거의 모든 작업에 사용 |
| **임베딩** | text-embedding-3-small, RAG 인덱스용 |

---

**작성 일자**: 2026-05-24  
**프로젝트**: paper_read_project_pdf (Transformer 논문 읽기 보조 시스템)
