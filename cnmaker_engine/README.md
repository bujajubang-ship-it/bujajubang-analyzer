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

