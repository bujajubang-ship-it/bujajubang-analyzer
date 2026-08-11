# 부자주방 소싱 분석기 — 작업 규칙

쿠팡 판매를 돕는 사내 웹앱이다. 실제로 장사에 쓰고 있으니 **깨지면 바로 매출에 영향이 간다.**

## 이 프로젝트가 하는 일

한 개의 FastAPI 앱(`main.py`)에 탭이 여러 개 붙어 있는 구조다. 화면은 `static/`에 있다.

| 탭 | 화면 | 서버 |
|---|---|---|
| 쿠팡 품목 진행상황 · 소싱 추천 · 키워드 분석 | `static/index.html`, `app.js` | `main.py` |
| CN메이커 (1688 → 상세페이지) | `static/cnmaker.html` | `main.py`, `page_maker.py` |
| 단가/마진 · 마진분석 · 카테고리 추천 | `static/index.html` | `main.py` |
| **📊 시장조사** | `static/market.html` | `market_api.py` |
| **📝 상품명 처방** | `static/market.html` | `name_doctor.py` |
| 자금 대시보드 (별도 잠금) | `static/jageum.html` | `main.py` |

## 반드시 지킬 것

**1. `main` 브랜치에 직접 push 하지 않는다.**
Render가 `main`을 보고 **자동 배포**한다(`render.yaml`의 `autoDeploy: true`).
main에 올리는 순간 실서비스가 바뀐다. 작업은 항상 새 브랜치에서 하고, 사장님 확인 후 병합한다.

```bash
git switch -c fix/무엇을-고치는지
# 작업 후
git push -u origin fix/무엇을-고치는지
```

**2. API 키를 코드에 쓰지 않는다.**
키는 전부 Render 환경변수와 서버 `.env`에만 있다. `.env`는 git에 올리지 않는다(.gitignore).
로컬에 키가 없어도 개발할 수 있게 되어 있다 — 없으면 그 기능만 안 될 뿐 앱은 뜬다.

**3. 데이터 파일은 git에 넣지 않는다.**
`wing_slim.db`(54MB) · `cat_meta.json` · `my_products.json`은 영구 디스크(`/var/data`)에 있다.
로컬에 없으면 시장조사 탭이 "데이터가 아직 안 올라왔습니다"라고 뜬다. 정상이다.

**4. 로그인 잠금을 풀지 않는다.**
사업 데이터라 전 화면에 잠금이 걸려 있다. 새 화면·API를 만들면 잠금을 반드시 같이 건다.
- 화면: `if not _site_auth(request): return FileResponse("static/site_login.html")`
- API: `APIRouter(..., dependencies=[Depends(_guard)])`
- 예외는 서버끼리 부르는 업로드 API뿐이고, 그건 `x-secret` 헤더로 따로 막는다.

**5. 이 저장소 밖은 건드리지 않는다.**
쿠팡 재고 자동화(매시간 2,924개 채움)·쿠폰 재발급·이카운트 수집은 **라이트세일 서버에만** 있고
git에 없다. 그쪽은 매출에 직결되므로 사장님이 직접 다룬다.

## 로컬에서 띄우기

```bash
pip install -r requirements.txt
DATA_DIR=. python3 -m uvicorn main:app --reload --port 8300
```
`http://localhost:8300` — 로그인 화면이 뜨면 정상이다(계정은 사장님께).

## 코드 스타일

- 기존 코드 모양을 따라간다. 요청하지 않은 리팩터링·기능 추가는 하지 않는다.
- 주석은 **왜 이렇게 했는지**를 적는다. 무엇을 하는지는 코드가 이미 말한다.
- 화면 글자는 장사하는 사람이 읽는다. 개발 용어 대신 쉬운 말로 쓴다.
  (예: "쿼리 실패" ❌ → "데이터를 못 불러왔습니다" ✅)
- 색은 `#D70010`(부자주방 빨강), 글꼴은 Pretendard.

## 이미 밟은 함정 (다시 밟지 말 것)

- **`/api/pipeline/{id}` 같은 경로가 있으면 `/api/pipeline/remind`는 405가 난다.**
  FastAPI가 `remind`를 id로 잡는다. 새 경로는 `-`로 붙인다(`/api/pipeline-remind`).
- **SQLite FTS5 trigram은 3글자 이상만 찾는다.** '냄비'(2자)를 넣으면 조용히 0건이 나온다.
  2글자가 섞이면 LIKE로 넘긴다(`market_api.add_text_filter`).
- **쿠팡 원본 이미지는 1장에 1MB쯤 된다.** 목록에선 `image11.coupangcdn.com/image/`를
  `thumbnail11.coupangcdn.com/thumbnails/remote/120x120ex/image/`로 바꿔 쓴다(20KB).
- **시장조사 판매량은 상품(productId) 단위다.** 옵션마다 같은 숫자가 박혀 있어서
  그냥 합치면 3배 넘게 부풀려진다. 합계·정렬엔 `sales_dup = 0` 조건이 반드시 붙어야 한다.
- **상품명 처방에 남의 브랜드명을 넣으면 안 된다.** 쿠팡 제재 대상이다.
  브랜드 목록으로 거르되, 브랜드명 '전체'와 똑같은 말만 뺀다 —
  안 그러면 '업소용냉장고 우성' 때문에 '업소용'(검색량 59,099) 같은 핵심어까지 사라진다.
