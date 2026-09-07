# CN메이커 Lightsail 작업 서버

1688/CN인사이더 로그인·상품 수집과 상세페이지 이미지 생성을 담당하는 장시간 작업 서버다.
Render의 `main.py`는 이 서버에 작업을 요청하고 상태와 결과를 중계한다.

## 저장소에 포함한 것

- `server.py`: HTTP 작업 API와 백그라운드 작업 관리
- `pipeline.py`: Playwright 로그인·수집과 기존 Pillow 합성
- `gptmaker.py`: CN인사이더 및 카페24 이미지 생성 흐름
- `page_maker.py`: 이미지 분석·템플릿 합성 도구
- `cn_transform.py`: 원본 이미지 변환

운영 서버의 환경변수, 로그인 상태, 생성 결과, 히스토리, 진단 파일과 백업본은 포함하지 않는다.

## 필요한 환경변수

`env.example`을 참고해 Lightsail의 `/home/ubuntu/cnmaker/cn.env`에서 관리한다.
실제 값은 Git에 넣지 않는다.

## 설치

```bash
python3 -m venv .venv
.venv/bin/pip install -r cnmaker_engine/requirements.txt
.venv/bin/playwright install chromium
```

## 배포 원칙

1. 작업 브랜치에서 테스트한다.
2. 사장님 확인 후 GitHub에 병합한다.
3. Lightsail에서 새 버전을 별도 폴더에 내려받는다.
4. 기존 `cnmaker.service`를 바꾸기 전에 새 포트로 상태 확인한다.
5. 정상일 때만 systemd 실행 경로를 전환한다.
6. 문제 발생 시 기존 `/home/ubuntu/cnmaker`로 즉시 되돌린다.

현재 운영 중인 `/home/ubuntu/cnmaker` 폴더는 자동으로 덮어쓰지 않는다.

## 저해상도 시안 편집 (2026-09-07)

`draft_editor.py`가 기존 10개 구간 구성과 대표 썸네일을 저품질로 먼저 생성한다. 새 화면은 `/cnmaker/api/drafts`를 사용하며 Render의 `cn_draft_api.py`가 직원 로그인과 Lightsail 인증을 연결한다. 기존 생성 API와 이전 히스토리는 유지한다.

- 상태와 이미지: `results/drafts/<12자리 작업번호>/`. 배포 시 이 폴더를 보존한다.
- `low` 생성은 실제 이미지 API의 낮은 품질로 요청하며 시안은 폭 430으로 저장한다.
- `high`는 선택한 구간만 현재 이미지를 참조하여 생성한다. 상세 구간 폭 860, 대표 썸네일 폭 1000으로 저장한다.
- 부분 수정은 현재 이미지를 첫 번째 참조로 사용한다. 성공한 저해상도 수정은 이전 고화질의 선택 상태를 해제하며 이전 파일은 보존한다.
- 실패 시 성공한 이미지를 지우지 않는다. 재시도는 실패한 작업의 품질 및 수정 요청을 유지한다.
- 상품 정보·글 기획 저장은 이미지를 자동 재생성하지 않는다. 이후 재생성에 적용된다.
- 서버 재시작으로 중단된 작업은 상태 조회 시 재시도 가능한 상태로 표시한다.
- 신규 배포 시 `server.py`와 `draft_editor.py`를 함께 반영한다.

검증: `python -m unittest -q test_cn_draft_editor test_cnmaker_restore test_cnmaker_engine_security`. 테스트는 이미지 API를 모의 처리한다.

## 링크 사진과 판매 색상 (2026-09-07)

생성 전 `판매 색상`을 쉼표로 구분하고 `대표 색상`을 지정할 수 있다. 색상 기준 사진은 제품 사진과 별도로 최대 3장 등록한다. 캡처만 등록하면 확인된 색상을 분석하며, 색상명도 입력하면 해당 이름으로 판매 옵션을 제한하고 캡처를 색조 기준으로 사용한다. 모두 비우면 원본 색상을 유지한다.

- `source_photos.py`가 CN인사이더 상품 상세 경로와 상품 ID를 확인하고, 옵션·상세·지연 로딩 사진을 수집한다. 로그인·메인 화면으로 돌아간 경우 재로그인 후 재검증한다. 링크 수집 실패는 이미지 생성 전에 중단한다.
- 원본 링크 사진은 중복을 제외해 최대 32개 수집한다. 긴 상세 이미지는 글자와 구조를 읽을 수 있도록 나누며 제품 분석용 조각은 직접 올린 사진을 포함해 최대 64장이다. 제한이나 다운로드 실패는 화면에 표시한다.
- 사진별 제품 동일성·구도·색상·기능 근거·원문/번역을 분석한다. 다른 모델·추천 상품으로 분류된 사진은 생성에 전달하지 않는다. 색상만 다른 같은 모델의 구도는 활용한다.
- 구간별 참고 계획과 기능 근거에 맞는 제품 사진 최대 12장, 색상 기준 최대 3장, 부분 수정·고화질 작업의 현재 이미지 1장을 전달한다. 전송 함수의 기존 4장 잘라내기는 제거했다. [OpenAI 이미지 편집 입력 한도](https://developers.openai.com/api/reference/resources/images/methods/edit)는 16장이다.
- 일반 구간은 대표 색상, 컬러·사이즈 구간은 지정한 판매 색상 전체를 사용한다. 다른 색상의 원본은 구도와 제품 구조의 근거로 사용하되 색상만 변경하도록 지시한다. 색상 기준 캡처의 제품 형태·배경·구도를 복사하지 않는다.
- 각 구간의 `reference_ids`, `color_sample_ids`, `reference_count`는 마지막 성공한 생성에 실제 전달한 사진을 기록한다. 실패한 새 시도의 참고 목록은 `reference_attempt`에 별도로 남겨 기존 이미지의 출처를 덮지 않는다.
- 참고 사진은 기존 인증된 `/cnmaker/drafts/image?id=...&asset=...` 경로로만 조회한다. 서버 등록 ID만 허용하며 공개 응답에서 원본 URL과 로컬 파일 경로는 제외한다.
- 기존 시안은 처음 재생성할 때 링크 사진을 다시 수집·분석한다. 기획과 기존 성공 이미지는 보존한다. 기획의 판매 색상 변경만으로 이미지를 자동 재생성하지 않는다.

이번 서버 변경은 `gptmaker.py`, `draft_editor.py`, `source_photos.py`를 함께 반영해야 한다. `results/drafts`, 환경파일, 로그인 상태와 히스토리는 유지한다. 색상 변환과 제품 형태 보존은 모델에 지시하는 방식이므로 실제 생성 시안을 확인해야 한다.

검증: `python -m unittest -q test_cn_draft_editor test_cn_source_photos test_cnmaker_restore test_cnmaker_engine_security`. 이미지 생성과 사진 분석 API는 모의 처리한다.

