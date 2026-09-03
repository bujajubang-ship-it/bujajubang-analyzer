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

