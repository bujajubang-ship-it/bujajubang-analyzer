#!/usr/bin/env python3
# 쿠팡 자동화 감시견: API가 죽거나(키 만료 등) 작업이 크래시하면 STOCK_ALERT_TO(naver)로 메일.
# 하루 몇 번 크론 실행. 정상이면 조용, 문제일 때만 메일(18시간 중복 억제). --test: 설치확인 메일 1회.
import os, sys, json, time, datetime
HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "watchdog_state.json")
NOW = datetime.datetime.now()
stamp = NOW.strftime("%Y-%m-%d %H:%M")
import wing_server as w

if "--test" in sys.argv:
    w.send_email("[부자홀딩스] ✅ 자동화 감시 설치 완료",
                 f"[{stamp}] 쿠팡 자동화 감시견이 켜졌습니다.\n"
                 f"앞으로 매일 작업이 실패하거나 쿠팡 API(키)가 먹통이 되면 바로 이 주소로 알려드립니다.\n"
                 f"현재 상태: 정상. (이 메일은 설치 확인용 1회 발송입니다.)")
    print(f"{stamp} [watchdog] 설치확인 메일 발송", flush=True)
    sys.exit(0)

def load_state():
    try: return json.load(open(STATE))
    except Exception: return {}
def save_state(s):
    try: json.dump(s, open(STATE, "w"))
    except Exception: pass

state = load_state()
def recently_alerted(key, hours=18):
    return (time.time() - state.get(key, 0)) < hours * 3600
def mark(key):
    state[key] = time.time()

alerts = []

# 1) API/키 헬스체크 — 인증이 살아있는지
try:
    code, d = w.req("GET", f"/v2/providers/seller_api/apis/api/v1/marketplace/seller-products?vendorId={w.VID}&maxPerPage=1")
except Exception as e:
    code, d = -1, str(e)
if code != 200:
    if not recently_alerted("api"):
        alerts.append(("[부자홀딩스] 🚨 쿠팡 API 먹통 — 자동화 정지",
            f"[{stamp}] 쿠팡 API 인증/응답 실패 (HTTP {code}).\n응답: {str(d)[:200]}\n\n"
            f"→ 재고 최적화·쿠폰·리포트 등 모든 자동작업이 멈춥니다.\n"
            f"※ 대개 WING API 키 만료입니다. WING → 판매자정보 → OPEN API [재발급] → "
            f"서버 ~/wing/.env 의 COUPANG_WING_SECRET_KEY 교체 후 확인하세요."))
        mark("api")

# 2) 최근(6h 내) 작업 로그에서 크래시(Traceback) 감지
LOGS = ["stock_full.log", "hv_bump.log", "push_coupang.log", "monitor.log", "renew.log", "report.log", "stock_daily.log"]
for lg in LOGS:
    p = os.path.join(HERE, lg)
    if not os.path.exists(p):
        continue
    if time.time() - os.path.getmtime(p) > 6 * 3600:
        continue
    try:
        with open(p, encoding="utf-8", errors="ignore") as f:
            tail = f.read()[-4000:]
    except Exception:
        continue
    if "Traceback (most recent call last)" in tail:
        key = f"crash:{lg}:{int(os.path.getmtime(p))}"
        if not recently_alerted("crash:" + lg):
            snippet = tail[tail.rfind("Traceback (most recent call last)"):][:1200]
            alerts.append((f"[부자홀딩스] 🚨 자동작업 오류 — {lg}",
                f"[{stamp}] '{lg}' 작업에서 오류(크래시)가 감지됐습니다.\n\n{snippet}"))
            mark("crash:" + lg)

for subj, body in alerts:
    try:
        w.send_email(subj, body)
        print(f"{stamp} [watchdog] 경고발송: {subj}", flush=True)
    except Exception as e:
        print(f"{stamp} [watchdog] 발송실패: {e}", flush=True)
if not alerts:
    print(f"{stamp} [watchdog] 정상 (문제 없음)", flush=True)
save_state(state)
