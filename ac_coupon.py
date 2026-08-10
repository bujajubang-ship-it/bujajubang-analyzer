#!/usr/bin/env python3
# 쿠폰 없는 옵션(ob_have_vids에 없는) 전부에 "정가↑+쿠폰"을 안전하게 적용.
# 안전: 쿠폰 연결 성공(DONE)한 경우에만 정가 인상 → 실구매가 불변(현재가 유지)+빨간딱지.
#       연결 실패(기존쿠폰 존재)면 정가 안 건드리고 방금 만든 쿠폰은 만료(정리).
# 재고: 판매가>=270000 → 5, 그외 → 9.  쿠폰명 AC{vid}.
# 사용: python3 ac_coupon.py [LIMIT] [--apply]
import wing_server as w
import time, json, random, sys, datetime
CONTRACT=276433; START="2026-06-28 00:00:00"; END="2027-06-28 23:59:00"; HV=270000
arg=[a for a in sys.argv[1:] if a.isdigit()]
LIMIT=int(arg[0]) if arg else None
APPLY="--apply" in sys.argv
rng=random.Random(77)
def rq(m,p,b=None):
    for a in range(4):
        c,d=(w.req(m,p,b) if b is not None else w.req(m,p))
        if c in (429,-1): time.sleep(2+a*2); continue
        return c,d
    return c,d
def reqstatus(rid):
    c,d=rq("GET",f"/v2/providers/fms/apis/api/v1/vendors/{w.VID}/requested/{rid}")
    return (d.get("data",{}) or {}).get("content",{}) if isinstance(d,dict) else {}
def expire(cid):
    rq("PUT",f"/v2/providers/fms/apis/api/v1/vendors/{w.VID}/coupons/{cid}?action=expire")
have=set(json.load(open("/home/ubuntu/wing/ob_have_vids.json")))
items=w.load_items()
targets=[it for it in items if it["vid"] not in have]
if LIMIT: targets=targets[:LIMIT]
print(f"{'[APPLY]' if APPLY else '[DRY]'} 쿠폰없는(추정) 대상 {len(targets)}개", flush=True)
done=skip_exist=fail=cheap=0; log=[]
def chunks(l,n):
    for i in range(0,len(l),n): yield l[i:i+n]
for ci,chunk in enumerate(chunks(targets,40)):
    plans=[]
    for it in chunk:
        vid=it["vid"]
        c,d=rq("GET",f"/v2/providers/seller_api/apis/api/v1/marketplace/vendor-items/{vid}/inventories")
        data=d.get("data") or {} if isinstance(d,dict) else {}
        price=data.get("salePrice")
        if not isinstance(price,int) or price<5000: cheap+=1; continue
        X=rng.uniform(0.025,0.035) if price>1000000 else rng.uniform(0.08,0.12)
        new=w.natural(price*(1+X)); disc=new-price
        if disc<10: cheap+=1; continue
        plans.append({"vid":vid,"name":it.get("name","")[:30],"price":price,"new":new,"disc":disc,
                      "tgt":5 if price>=HV else 9})
    if not APPLY:
        for p in plans[:99999]: log.append(p)
        continue
    # 1) 쿠폰생성
    for p in plans:
        body={"contractId":CONTRACT,"name":f"AC{p['vid']}","discount":p["disc"],"type":"PRICE","maxDiscountPrice":p["disc"],"startAt":START,"endAt":END,"wowExclusive":False}
        c,d=rq("POST",f"/v2/providers/fms/apis/api/v2/vendors/{w.VID}/coupon",body)
        p["crid"]=((d.get("data",{}) or {}).get("content",{}) or {}).get("requestedId") if isinstance(d,dict) else None
        time.sleep(0.35)
    # 2) couponId
    for _ in range(10):
        pend=[p for p in plans if p.get("crid") and not p.get("cid")]
        if not pend: break
        for p in pend:
            st=reqstatus(p["crid"])
            if st.get("status")=="DONE": p["cid"]=st.get("couponId")
            elif st.get("status")=="FAIL": p["cid"]=None; p["crid"]=None
        time.sleep(3)
    # 3) 연결
    for p in plans:
        if not p.get("cid"): continue
        c,d=rq("POST",f"/v2/providers/fms/apis/api/v1/vendors/{w.VID}/coupons/{p['cid']}/items",{"vendorItems":[p["vid"]]})
        p["arid"]=((d.get("data",{}) or {}).get("content",{}) or {}).get("requestedId") if isinstance(d,dict) else None
        time.sleep(0.35)
    # 4) 연결확인 → 성공:정가인상+재고 / 실패:쿠폰만료
    for _ in range(14):
        pend=[p for p in plans if p.get("arid") and p.get("res") is None]
        if not pend: break
        for p in pend:
            st=reqstatus(p["arid"])
            if st.get("status")=="DONE" and (st.get("succeeded",0) or 0)>=1: p["res"]="ok"
            elif st.get("status")=="FAIL": p["res"]="exist"
        time.sleep(4)
    for p in plans:
        if p.get("res")=="ok":
            rq("PUT",f"/v2/providers/seller_api/apis/api/v1/marketplace/vendor-items/{p['vid']}/prices/{p['new']}")
            rq("PUT",f"/v2/providers/seller_api/apis/api/v1/marketplace/vendor-items/{p['vid']}/quantities/{p['tgt']}")
            done+=1
        elif p.get("res")=="exist":
            if p.get("cid"): expire(p["cid"])  # 기존쿠폰 있음 → 내 쿠폰 정리, 정가 안건드림
            skip_exist+=1
        else:
            if p.get("cid"): expire(p["cid"])
            fail+=1
    print(f"  청크 {ci+1}: 누적 적용 {done} / 기존쿠폰스킵 {skip_exist} / 실패 {fail}", flush=True)
if not APPLY:
    print(f"[DRY] 적용예정 {len(log)}개 (저가제외 {cheap}). 샘플:")
    for p in log[:15]: print(f"  {p['name'][:30]:30} {p['price']:>9,} → 정가 {p['new']:>9,} 쿠폰 -{p['disc']:,} 재고 {p['tgt']}")
    print("적용: --apply")
else:
    print(f"\n[완료] 쿠폰적용 {done} / 기존쿠폰있어스킵 {skip_exist} / 실패 {fail} / 저가제외 {cheap}", flush=True)
    try: w.send_email("[부자홀딩스] 쿠폰 일괄적용 완료", f"쿠폰없던 옵션 처리: 신규부착 {done} / 기존스킵 {skip_exist} / 실패 {fail}")
    except: pass
