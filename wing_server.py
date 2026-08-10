#!/usr/bin/env python3
# 부자홀딩스 쿠팡 서버 (Lightsail 24시간) — 표준 라이브러리만 사용
# 사용: python3 wing_server.py [monitor|build|testmail|renew]
import os, sys, json, time, datetime, hmac, hashlib, smtplib, ssl
import urllib.request, urllib.error
from email.mime.text import MIMEText

HERE = os.path.dirname(os.path.abspath(__file__))
ENV = {}
for line in open(os.path.join(HERE, ".env"), encoding="utf-8"):
    line = line.strip()
    if "=" in line and not line.startswith("#"):
        k, v = line.split("=", 1); ENV[k] = v
AK = ENV["COUPANG_WING_ACCESS_KEY"]; SK = ENV["COUPANG_WING_SECRET_KEY"]; VID = ENV["COUPANG_WING_VENDOR_ID"]
BASE = "https://api-gateway.coupang.com"
THRESHOLD = 5
CONTRACT = 276433
COUPON_END = "2027-06-24 23:59:00"   # 재발급 쿠폰 종료일(1년)

def auth(method, pq):
    parts = pq.split("?", 1); path = parts[0]; q = parts[1] if len(parts) > 1 else ""
    dt = datetime.datetime.utcnow(); sd = dt.strftime("%y%m%d") + "T" + dt.strftime("%H%M%S") + "Z"
    sig = hmac.new(SK.encode(), (sd + method + path + q).encode(), hashlib.sha256).hexdigest()
    return f"CEA algorithm=HmacSHA256, access-key={AK}, signed-date={sd}, signature={sig}"

def req(method, pq, body=None):
    data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
    r = urllib.request.Request(BASE + pq, method=method, data=data,
                               headers={"Authorization": auth(method, pq), "Content-Type": "application/json;charset=UTF-8"})
    try:
        with urllib.request.urlopen(r, timeout=40) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try: return e.code, json.loads(e.read().decode())
        except: return e.code, "err"
    except Exception as e:
        return -1, str(e)

def send_email(subject, body):
    msg = MIMEText(body, _charset="utf-8")
    msg["Subject"] = subject; msg["From"] = ENV["EMAIL_FROM"]; msg["To"] = ENV["STOCK_ALERT_TO"]
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30, context=ssl.create_default_context()) as s:
        s.login(ENV["EMAIL_FROM"], ENV["EMAIL_PASSWORD"])
        s.sendmail(ENV["EMAIL_FROM"], ENV["STOCK_ALERT_TO"], msg.as_string())

def approved_products():
    sids = []; token = ""
    while True:
        code, d = req("GET", f"/v2/providers/seller_api/apis/api/v1/marketplace/seller-products?vendorId={VID}&maxPerPage=50" + (f"&nextToken={token}" if token else ""))
        if code != 200 or not isinstance(d, dict): break
        for p in d.get("data", []):
            if p.get("statusName") == "승인완료": sids.append(p["sellerProductId"])
        token = d.get("nextToken", "")
        if not token: break
    return sids

def build_items():
    print("상품 목록 수집 중...", flush=True)
    sids = approved_products()
    print(f"승인상품 {len(sids)}개 → 옵션ID 수집 중...", flush=True)
    vids = []
    for sid in sids:
        code, d = req("GET", f"/v2/providers/seller_api/apis/api/v1/marketplace/seller-products/{sid}")
        if code == 200 and isinstance(d, dict):
            nm = d.get("data", {}).get("sellerProductName", "")
            for it in d.get("data", {}).get("items", []):
                if it.get("vendorItemId"):
                    vids.append({"vid": it["vendorItemId"], "name": nm, "price": it.get("salePrice", 0) or 0})
        time.sleep(0.05)
    json.dump(vids, open(os.path.join(HERE, "items.json"), "w"), ensure_ascii=False)
    print(f"옵션 {len(vids)}개 저장(items.json)", flush=True)
    return vids

def load_items():
    p = os.path.join(HERE, "items.json")
    if not os.path.exists(p): return build_items()
    return json.load(open(p, encoding="utf-8"))

def monitor():
    items = load_items(); low = []
    for it in items:
        code, d = req("GET", f"/v2/providers/seller_api/apis/api/v1/marketplace/vendor-items/{it['vid']}/inventories")
        if code == 200 and isinstance(d, dict):
            q = (d.get("data") or {}).get("amountInStock")
            if isinstance(q, int) and q < THRESHOLD:
                low.append((it["name"], q))
        time.sleep(0.05)
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    if low:
        body = f"[{stamp}] 재고 {THRESHOLD}개 미만 {len(low)}건\n\n" + "\n".join(f"- {n[:40]} : {q}개" for n, q in low)
        send_email(f"[부자홀딩스] 재고부족 {len(low)}건 (재입고 필요)", body)
        print(f"{stamp} 알림 발송: {len(low)}건", flush=True)
    else:
        print(f"{stamp} 재고 정상 (부족 0건)", flush=True)

# ── 쿠폰 자동 갱신 ──
def natural(n):
    n = int(round(n))
    if n >= 10000: n = round(n / 100) * 100 - 10
    elif n >= 1000: n = round(n / 10) * 10 - 10
    return max(n, 100)

def our_active_coupons():
    """이름이 BJ/RN 으로 시작하는(우리가 만든) APPLIED 쿠폰 수."""
    cnt = 0
    for st in ["APPLIED", "PARTIAL_APPLIED"]:
        for pg in range(1, 30):
            code, d = req("GET", f"/v2/providers/fms/apis/api/v2/vendors/{VID}/coupons?status={st}&page={pg}&size=50")
            cont = (d.get("data", {}) or {}).get("content", []) if isinstance(d, dict) else []
            if not cont: break
            for c in cont:
                nm = str(c.get("promotionName", ""))
                if nm.startswith("BJ") or nm.startswith("RN"): cnt += 1
    return cnt

def renew():
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    active = our_active_coupons()
    if active > 5:
        print(f"{stamp} 갱신 불필요 (활성 쿠폰 {active}개)", flush=True); return
    print(f"{stamp} 활성 쿠폰 {active}개 → 만료 감지, 재발급 시작", flush=True)
    sids = approved_products()
    made = 0; plans = []
    for sid in sids:
        code, d = req("GET", f"/v2/providers/seller_api/apis/api/v1/marketplace/seller-products/{sid}")
        if code != 200 or not isinstance(d, dict): continue
        it = next((x for x in d.get("data", {}).get("items", []) if x.get("vendorItemId")), None)
        if not it: continue
        P = it.get("salePrice", 0) or 0
        if P < 5000: continue
        pct = 0.03 if P > 1030000 else 0.10                 # 현재가(이미 인상가) 기준 티어
        disc = P - natural(P / (1 + pct))                    # 인상분만큼 할인 → 가격은 그대로
        if disc < 10: continue
        cname = f"RN{made}"
        body = {"contractId": CONTRACT, "name": cname, "discount": disc, "type": "PRICE",
                "maxDiscountPrice": disc, "startAt": "2026-06-24 00:00:00", "endAt": COUPON_END, "wowExclusive": False}
        c, r = req("POST", f"/v2/providers/fms/apis/api/v2/vendors/{VID}/coupon", body)
        if isinstance(r, dict) and r.get("data", {}).get("success"):
            plans.append({"vid": it["vendorItemId"], "cname": cname}); made += 1
        time.sleep(0.35)
    # couponId 매핑
    time.sleep(20); name2id = {}
    for st in ["WAIT_APPLY", "APPLIED"]:
        for pg in range(1, 40):
            c, d = req("GET", f"/v2/providers/fms/apis/api/v2/vendors/{VID}/coupons?status={st}&page={pg}&size=50")
            cont = (d.get("data", {}) or {}).get("content", []) if isinstance(d, dict) else []
            if not cont: break
            for x in cont:
                if str(x.get("promotionName", "")).startswith("RN"): name2id[x["promotionName"]] = x["couponId"]
    attached = 0
    for p in plans:
        cid = name2id.get(p["cname"])
        if not cid: continue
        req("POST", f"/v2/providers/fms/apis/api/v1/vendors/{VID}/coupons/{cid}/items", {"vendorItems": [p["vid"]]})
        attached += 1; time.sleep(0.35)
    send_email("[부자홀딩스] 쿠폰 자동 갱신 완료", f"[{stamp}] 쿠폰 만료 감지 → 1년 쿠폰 {attached}개 재발급 완료.")
    print(f"{stamp} 재발급 {attached}개 완료", flush=True)

# ── 밤사이 판매 리포트 (아침 7시) : 어제 18:00 ~ 지금 ──
def report():
    now = datetime.datetime.now()
    start = (now - datetime.timedelta(days=1)).replace(hour=18, minute=0, second=0, microsecond=0)
    d_from = start.date(); d_to = now.date()
    normal = {}; rocket = {}; seen = set()
    for st in ["ACCEPT", "INSTRUCT", "DEPARTURE", "DELIVERING", "FINAL_DELIVERY"]:
        token = ""
        for _ in range(40):
            pq = f"/v2/providers/openapi/apis/api/v4/vendors/{VID}/ordersheets?createdAtFrom={d_from}&createdAtTo={d_to}&status={st}&maxPerPage=50" + (f"&nextToken={token}" if token else "")
            code, d = req("GET", pq)
            if code != 200 or not isinstance(d, dict): break
            for box in d.get("data", []):
                try:
                    oa = datetime.datetime.strptime(box.get("orderedAt", "")[:19], "%Y-%m-%dT%H:%M:%S")
                except Exception:
                    continue
                if not (start <= oa <= now): continue
                tgt = normal if box.get("shipmentType") == "THIRD_PARTY" else rocket
                for oi in box.get("orderItems", []):
                    cnt = (oi.get("shippingCount", 0) or 0) - (oi.get("cancelCount", 0) or 0)
                    if cnt <= 0: continue
                    key = (box.get("orderId"), oi.get("vendorItemId"))
                    if key in seen: continue
                    seen.add(key)
                    nm = oi.get("sellerProductName", "")
                    e = tgt.setdefault(nm, [0, 0]); e[0] += cnt; e[1] += (oi.get("orderPrice", 0) or 0)
            token = d.get("nextToken", "")
            if not token: break
    def fmt(dct):
        if not dct: return "  (판매 없음)"
        return "\n".join(f"  - {n[:38]} : {v[0]}개 / {v[1]:,}원" for n, v in sorted(dct.items(), key=lambda x: -x[1][0]))
    nq = sum(v[0] for v in normal.values()); nr = sum(v[1] for v in normal.values())
    rq = sum(v[0] for v in rocket.values()); rr = sum(v[1] for v in rocket.values())
    body = (f"기간: {start.strftime('%m/%d %H:%M')} ~ {now.strftime('%m/%d %H:%M')}\n\n"
            f"■ 일반(윙) 판매 — 총 {nq}개 / {nr:,}원\n{fmt(normal)}\n\n"
            f"■ 로켓그로스 판매 — 총 {rq}개 / {rr:,}원\n{fmt(rocket)}\n")
    if rq == 0:
        body += "\n※ 로켓그로스는 현재 사용 상품이 없어 0건입니다 (시작 시 자동 표시)."
    send_email(f"[부자홀딩스] 밤사이 판매 {nq+rq}개 ({nr+rr:,}원)", body)
    print(f"{now.strftime('%Y-%m-%d %H:%M')} 리포트 발송: 일반 {nq} / 로켓 {rq}", flush=True)

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "monitor"
    if cmd == "build": build_items()
    elif cmd == "testmail": send_email("[부자홀딩스] 서버 테스트 메일", "Lightsail 서버에서 보낸 테스트입니다.")
    elif cmd == "renew": renew()
    elif cmd == "report": report()
    else: monitor()
