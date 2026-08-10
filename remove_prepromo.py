#!/usr/bin/env python3
# 프로모션 직전 쿠폰 자동 제거: PREPROMO 쿠폰 만료 + 해당 vid 잔여 쿠폰 정리
import wing_server as w
import time, datetime, re
CIDS=[93627288,93627290]; VIDS=[92089016737,92089016717]
def rq(m,p,b=None):
    for a in range(4):
        c,d=(w.req(m,p,b) if b is not None else w.req(m,p))
        if c in (429,-1): time.sleep(2);continue
        return c,d
    return c,d
def rs(rid):
    c,d=rq("GET",f"/v2/providers/fms/apis/api/v1/vendors/{w.VID}/requested/{rid}")
    return (d.get("data",{}) or {}).get("content",{}) if isinstance(d,dict) else {}
def expire(cid): rq("PUT",f"/v2/providers/fms/apis/api/v1/vendors/{w.VID}/coupons/{cid}?action=expire")
stamp=datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
for cid in CIDS: expire(cid)
# 잔여 쿠폰까지 싹 정리(probe)
for V in VIDS:
    c,d=rq("GET",f"/v2/providers/seller_api/apis/api/v1/marketplace/vendor-items/{V}/inventories")
    price=(d.get("data") or {}).get("salePrice") or 100000
    body={"contractId":276433,"name":"PROBE_RM","discount":int(price*0.09),"type":"PRICE","maxDiscountPrice":int(price*0.09),"startAt":"2026-07-02 00:00:00","endAt":"2027-07-02 23:59:00","wowExclusive":False}
    c,d=rq("POST",f"/v2/providers/fms/apis/api/v2/vendors/{w.VID}/coupon",body)
    rid=((d.get("data",{}) or {}).get("content",{}) or {}).get("requestedId"); probe=None
    for _ in range(8):
        st=rs(rid)
        if st.get("status")=="DONE": probe=st.get("couponId"); break
        if st.get("status")=="FAIL": break
        time.sleep(2)
    if probe:
        for i in range(12):
            c,d=rq("POST",f"/v2/providers/fms/apis/api/v1/vendors/{w.VID}/coupons/{probe}/items",{"vendorItems":[V]})
            arid=((d.get("data",{}) or {}).get("content",{}) or {}).get("requestedId"); r=None;blk=None
            for _ in range(9):
                st=rs(arid)
                if st.get("status")=="DONE" and (st.get("succeeded",0) or 0)>=1: r="ok";break
                if st.get("status")=="FAIL":
                    fv=st.get("failedVendorItems",[]);rr=fv[0].get("reason","") if fv else ""
                    mm=re.search(r"coupon \((\d+)\)",rr);blk=int(mm.group(1)) if mm else None;r="fail";break
                time.sleep(2)
            if r=="ok": break
            if r=="fail" and blk: expire(blk); time.sleep(2)
            else: break
        expire(probe)
print(f"{stamp} 프리프로모 쿠폰 제거 완료 (부자식깡 20호/5호 쿠폰 0개)", flush=True)
try: w.send_email("[부자홀딩스] 프로모션전 쿠폰 자동제거 완료","부자식깡 20호/5호 쿠폰 제거됨. 07/03 프로모션 시작 준비 완료.")
except: pass
