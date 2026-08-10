#!/usr/bin/env python3
# 깨진(쿠폰죽은) 옵션 수동 수정. 사용: python3 fixvid.py <vid> [vid2 ...] [net=금액] [--apply]
# 동작: OB할인기록 있으면 그 크기로 / 없으면 정가 ~10%↑+쿠폰(실구매가=base 유지). 기존 죽은쿠폰은 만료후 재부착.
import wing_server as w
import time, json, sys, re
NET=next((int(a.split("=")[1]) for a in sys.argv if a.startswith("net=")), None)
VIDS=[int(x) for x in sys.argv[1:] if x.isdigit()]
APPLY="--apply" in sys.argv
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
    if not isinstance(cur,int): print(f"vid {V}: 가격조회실패"); continue
    obs=[int(dd) for _,dd,_ in OB.get(V,[])]
    if obs and not NET:
        disc=max(obs); newprice=None; mode="OB기반(인상유지)"
    else:
        base=NET if NET else cur
        newprice=w.natural(base*1.10); disc=newprice-base; mode=f"신규인상(net={base:,})"
    net_after=(cur if newprice is None else newprice)-disc
    print(f"vid {V} 현재가 {cur:,} | {mode} | {'정가→'+format(newprice,',')+' ' if newprice else ''}쿠폰 -{disc:,} → 실구매가 {net_after:,}")
    if not APPLY: continue
    body={"contractId":276433,"name":f"FX2{V}","discount":disc,"type":"PRICE","maxDiscountPrice":disc,"startAt":"2026-06-28 00:00:00","endAt":"2027-06-28 23:59:00","wowExclusive":False}
    c,d=rq("POST",f"/v2/providers/fms/apis/api/v2/vendors/{w.VID}/coupon",body)
    rid=((d.get("data",{}) or {}).get("content",{}) or {}).get("requestedId") if isinstance(d,dict) else None
    cid=None
    for _ in range(8):
        st=reqstatus(rid)
        if st.get("status")=="DONE": cid=st.get("couponId"); break
        if st.get("status")=="FAIL": break
        time.sleep(2)
    if not cid: print("  쿠폰생성 실패"); continue
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
    if r=="fail" and blk:
        print(f"  기존 죽은쿠폰 {blk} 만료 후 재시도"); expire(blk); time.sleep(2); r,blk=attach()
    if r=="ok":
        if newprice: rq("PUT",f"/v2/providers/seller_api/apis/api/v1/marketplace/vendor-items/{V}/prices/{newprice}")
        print(f"  ✅ 완료: 쿠폰 -{disc:,}" + (f" + 정가 {newprice:,}" if newprice else "") + f" → 실구매가 {net_after:,}")
    else: expire(cid); print(f"  ❌ 실패({r})")
