#!/usr/bin/env python3
# 전 상품의 "모든 옵션(vendorItem)"에 가격↑+쿠폰+재고9 적용.
# 이미 처리된 옵션(재고==9)은 스킵 → 중복 없음, 재실행 시 이어서 진행(idempotent).
import wing_server as w
import time, random, json

CONTRACT = 276433
END = "2027-06-25 23:59:00"
START = "2026-06-25 00:00:00"
rng = random.Random(2026)

def natural(n):
    n = int(round(n))
    if n >= 10000: n = round(n / 100) * 100 - 10
    elif n >= 1000: n = round(n / 10) * 10 - 10
    return max(n, 100)

def rq(method, path, body=None, tries=4):
    for a in range(tries):
        c, d = w.req(method, path, body) if body is not None else w.req(method, path)
        if c == 429 or c == -1:
            time.sleep(2 + a * 2); continue
        return c, d
    return c, d

def reqstatus(rid):
    c, d = rq("GET", f"/v2/providers/fms/apis/api/v1/vendors/{w.VID}/requested/{rid}")
    return (d.get("data", {}) or {}).get("content", {}) if isinstance(d, dict) else {}

def approved():
    sids = []; t = ""
    while True:
        c, d = rq("GET", f"/v2/providers/seller_api/apis/api/v1/marketplace/seller-products?vendorId={w.VID}&maxPerPage=50" + ("&nextToken=" + t if t else ""))
        if c != 200 or not isinstance(d, dict): break
        for p in d.get("data", []):
            if p.get("statusName") == "승인완료": sids.append(p["sellerProductId"])
        t = d.get("nextToken", "")
        if not t: break
    return sids

def main():
    sids = approved()
    print(f"승인상품 {len(sids)}개 — 전 옵션 적용 시작", flush=True)
    done = 0; failed = 0
    for pi, sid in enumerate(sids):
        c, d = rq("GET", f"/v2/providers/seller_api/apis/api/v1/marketplace/seller-products/{sid}")
        if c != 200 or not isinstance(d, dict): continue
        plans = []
        for it in d["data"].get("items", []):
            vid = it.get("vendorItemId")
            if not vid: continue
            c2, inv = rq("GET", f"/v2/providers/seller_api/apis/api/v1/marketplace/vendor-items/{vid}/inventories")
            iv = inv.get("data", {}) if isinstance(inv, dict) else {}
            price = iv.get("salePrice", 0) or 0; stock = iv.get("amountInStock")
            if stock == 9: continue          # 이미 처리됨
            if price < 5000: continue
            pct = rng.uniform(0.025, 0.035) if price > 1000000 else rng.uniform(0.08, 0.12)
            new = natural(price * (1 + pct)); disc = new - price
            if disc < 10: continue
            plans.append({"vid": vid, "price": price, "new": new, "disc": disc})
        if not plans:
            if (pi + 1) % 40 == 0: print(f"[{pi+1}/{len(sids)}] ... 누적 {done}", flush=True)
            continue
        # 1) 쿠폰 생성
        for p in plans:
            body = {"contractId": CONTRACT, "name": f"OB{sid}_{p['vid']}", "discount": p["disc"], "type": "PRICE",
                    "maxDiscountPrice": p["disc"], "startAt": START, "endAt": END, "wowExclusive": False}
            c, d = rq("POST", f"/v2/providers/fms/apis/api/v2/vendors/{w.VID}/coupon", body)
            p["crid"] = (d.get("data", {}) or {}).get("content", {}).get("requestedId") if isinstance(d, dict) else None
            time.sleep(0.5)
        # 2) couponId 확보
        for _ in range(8):
            pend = [p for p in plans if p.get("crid") and not p.get("cid")]
            if not pend: break
            for p in pend:
                st = reqstatus(p["crid"])
                if st.get("status") == "DONE": p["cid"] = st.get("couponId")
            if all(p.get("cid") for p in plans if p.get("crid")): break
            time.sleep(3)
        # 3) 옵션 연결
        for p in plans:
            if not p.get("cid"): continue
            c, d = rq("POST", f"/v2/providers/fms/apis/api/v1/vendors/{w.VID}/coupons/{p['cid']}/items", {"vendorItems": [p["vid"]]})
            p["arid"] = (d.get("data", {}) or {}).get("content", {}).get("requestedId") if isinstance(d, dict) else None
            time.sleep(0.5)
        # 4) 적용확인 → 가격/재고
        for _ in range(10):
            pend = [p for p in plans if p.get("arid") and not p.get("done") and not p.get("failed")]
            if not pend: break
            for p in pend:
                st = reqstatus(p["arid"])
                if st.get("status") == "DONE" and (st.get("succeeded", 0) or 0) >= 1:
                    rq("PUT", f"/v2/providers/seller_api/apis/api/v1/marketplace/vendor-items/{p['vid']}/prices/{p['new']}")
                    rq("PUT", f"/v2/providers/seller_api/apis/api/v1/marketplace/vendor-items/{p['vid']}/quantities/9")
                    p["done"] = True; done += 1
                elif st.get("status") == "FAIL":
                    p["failed"] = True; failed += 1
            time.sleep(3)
        print(f"[{pi+1}/{len(sids)}] {sid}: +{sum(1 for p in plans if p.get('done'))} (누적 {done}, 실패 {failed})", flush=True)
    print(f"완료: 적용 {done} / 실패 {failed}", flush=True)
    try: w.send_email("[부자홀딩스] 전체 옵션 쿠폰 적용 완료", f"모든 옵션에 가격↑·쿠폰·재고9 적용: {done}개 옵션 완료(실패 {failed}).")
    except Exception: pass
    print("BULK_DONE", flush=True)

main()
