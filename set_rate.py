#!/usr/bin/env python3
# 옵션을 목표 할인율로 재설정: 실구매가(net=orig) 유지, 정가↑ + 쿠폰으로 rate% 강조.
# orig = 현재가 - 기존OB할인액(없으면 현재가). 사용: python3 set_rate.py <vid> [rate=0.09] [--apply]
import wing_server as w
import time, json, sys, re
RATE=next((float(a.split("=")[1]) for a in sys.argv if a.startswith("rate=")), 0.09)
VIDS=[int(x) for x in sys.argv[1:] if x.isdigit()]; APPLY="--apply" in sys.argv
def rq(m,p,b=None):
    for a in range(4):
        c,d=(w.req(m,p,b) if b is not None else w.req(m,p))
        if c in (429,-1): time.sleep(2);continue
        return c,d
    return c,d
def reqstatus(rid):
    c,d=rq("GET",f"/v2/providers/fms/apis/api/v1/vendors/{w.VID}/requested/{rid}")
    return (d.get("data",{}) or {}).get("content",{}) if isinstance(d,dict) else {}
def expire(cid): rq("PUT",f"/v2/providers/fms/apis/api/v1/vendors/{w.VID}/coupons/{cid}?action=expire")
OB={int(k):v for k,v in json.load(open("/tmp/ob_map.json")).items()}
for V in VIDS:
    c,d=rq("GET",f"/v2/providers/seller_api/apis/api/v1/marketplace/vendor-items/{V}/inventories")
    cur=(d.get("data") or {}).get("salePrice") if isinstance(d,dict) else None
    if not isinstance(cur,int): print(f"vid {V} 가격조회실패"); continue
    obd=max([int(dd) for _,dd,_ in OB.get(V,[])], default=0)
    orig=cur-obd if obd else cur            # 실구매가(원래 의도가)
    newlist=w.natural(round(orig/(1-RATE))); disc=newlist-orig
    print(f"vid {V} 현재정가 {cur:,} (기존쿠폰 {obd:,} → 실구매가 {orig:,}) ⇒ 새정가 {newlist:,} 쿠폰 -{disc:,} (할인율 {disc/newlist*100:.1f}%) 실구매가 {orig:,}")
    if not APPLY: continue
    body={"contractId":276433,"name":f"R9{V}","discount":disc,"type":"PRICE","maxDiscountPrice":disc,"startAt":"2026-06-28 00:00:00","endAt":"2027-06-28 23:59:00","wowExclusive":False}
    c,d=rq("POST",f"/v2/providers/fms/apis/api/v2/vendors/{w.VID}/coupon",body)
    rid=((d.get("data",{}) or {}).get("content",{}) or {}).get("requestedId") if isinstance(d,dict) else None
    cid=None
    for _ in range(8):
        st=reqstatus(rid)
        if st.get("status")=="DONE": cid=st.get("couponId"); break
        if st.get("status")=="FAIL": break
        time.sleep(2)
    if not cid: print("  쿠폰생성실패"); continue
    def attach():
        c,d=rq("POST",f"/v2/providers/fms/apis/api/v1/vendors/{w.VID}/coupons/{cid}/items",{"vendorItems":[V]})
        arid=((d.get("data",{}) or {}).get("content",{}) or {}).get("requestedId") if isinstance(d,dict) else None
        for _ in range(9):
            st=reqstatus(arid)
            if st.get("status")=="DONE" and (st.get("succeeded",0) or 0)>=1: return "ok",None
            if st.get("status")=="FAIL":
                fv=st.get("failedVendorItems",[]); rs=fv[0].get("reason","") if fv else ""
                mm=re.search(r"coupon \((\d+)\)",rs); return "fail",(int(mm.group(1)) if mm else None)
            time.sleep(2)
        return "to",None
    r,blk=attach()
    if r=="fail" and blk: print(f"  기존쿠폰 {blk} 만료후 재시도"); expire(blk); time.sleep(2); r,blk=attach()
    if r=="ok":
        rq("PUT",f"/v2/providers/seller_api/apis/api/v1/marketplace/vendor-items/{V}/prices/{newlist}")
        print(f"  ✅ 완료: 정가 {newlist:,} + 쿠폰 -{disc:,} ({disc/newlist*100:.1f}%) → 실구매가 {orig:,}")
    else: expire(cid); print(f"  ❌ 실패 {r}")
