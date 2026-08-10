#!/usr/bin/env python3
# 쿠폰 리프레시: 옵션의 (죽었을수있는)기존쿠폰 치우고 정확한크기 새쿠폰 부착. net=원래가 유지.
# disc 결정: ob_map의 OB할인액(우선) → 없으면 스킵. 가격은 안 건드림(이미 인상된 상태 가정).
# 사용: python3 refresh.py [LIMIT] [--apply]
import wing_server as w
import time, json, sys, datetime
arg=[a for a in sys.argv[1:] if a.isdigit()]; LIMIT=int(arg[0]) if arg else None
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
# disc = 그 옵션의 OB할인액(가장 큰 것)
targets=[]
for vid,lst in OB.items():
    disc=max(int(d) for _,d,_ in lst) if lst else 0
    if disc>=100: targets.append((vid,disc))
targets.sort(key=lambda x:-x[1])
if LIMIT: targets=targets[:LIMIT]
print(f"{'[APPLY]' if APPLY else '[DRY]'} 리프레시 대상(OB보유) {len(targets)}개", flush=True)
done=blocked=fail=skip=0
for i,(vid,disc) in enumerate(targets):
    c,d=rq("GET",f"/v2/providers/seller_api/apis/api/v1/marketplace/vendor-items/{vid}/inventories")
    cur=(d.get("data") or {}).get("salePrice") if isinstance(d,dict) else None
    if not isinstance(cur,int) or disc>=cur: skip+=1; continue
    if not APPLY:
        if i<8: print(f"  vid {vid} 현재 {cur:,} 쿠폰 {disc:,} → net {cur-disc:,}")
        continue
    # 1) 새 쿠폰 생성
    body={"contractId":276433,"name":f"RF{vid}","discount":disc,"type":"PRICE","maxDiscountPrice":disc,"startAt":"2026-06-28 00:00:00","endAt":"2027-06-28 23:59:00","wowExclusive":False}
    c,d=rq("POST",f"/v2/providers/fms/apis/api/v2/vendors/{w.VID}/coupon",body)
    rid=((d.get("data",{}) or {}).get("content",{}) or {}).get("requestedId") if isinstance(d,dict) else None
    cid=None
    for _ in range(7):
        st=reqstatus(rid)
        if st.get("status")=="DONE": cid=st.get("couponId"); break
        if st.get("status")=="FAIL": break
        time.sleep(2)
    if not cid: fail+=1; continue
    # 2) 연결 시도 → CIR08면 블로커 만료 후 재시도
    def attach():
        c,d=rq("POST",f"/v2/providers/fms/apis/api/v1/vendors/{w.VID}/coupons/{cid}/items",{"vendorItems":[vid]})
        arid=((d.get("data",{}) or {}).get("content",{}) or {}).get("requestedId") if isinstance(d,dict) else None
        for _ in range(8):
            st=reqstatus(arid)
            if st.get("status")=="DONE" and (st.get("succeeded",0) or 0)>=1: return ("ok",None)
            if st.get("status")=="FAIL":
                fv=st.get("failedVendorItems",[]); reason=fv[0].get("reason","") if fv else ""
                import re
                mm=re.search(r"coupon \((\d+)\)",reason)
                return ("fail", int(mm.group(1)) if mm else None)
            time.sleep(2)
        return ("timeout",None)
    r,blk=attach()
    if r=="ok": done+=1
    elif r=="fail" and blk:
        expire(blk); time.sleep(2)
        r2,_=attach()
        if r2=="ok": done+=1; blocked+=1
        else: expire(cid); fail+=1
    else: expire(cid); fail+=1
    if (i+1)%40==0: print(f"  ...{i+1}/{len(targets)} 적용 {done}(블로커정리 {blocked}) 실패 {fail} 스킵 {skip}", flush=True)
print(f"\n[{'완료' if APPLY else 'DRY'}] 적용 {done} (기존쿠폰만료후성공 {blocked}) / 실패 {fail} / 스킵 {skip} / 대상 {len(targets)}", flush=True)
