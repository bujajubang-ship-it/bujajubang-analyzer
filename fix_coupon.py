#!/usr/bin/env python3
# 과다가격 31개에 쿠폰(=현재가-원래가) 생성·연결 → 실구매가 원래가 복구 + 할인딱지. 이름 FX{vid}.
import wing_server as w
import time, json, datetime, sys
CONTRACT=276433; START="2026-06-28 00:00:00"; END="2027-06-28 23:59:00"
APPLY = "--apply" in sys.argv
def rq(m,p,b=None):
    for a in range(4):
        c,d=(w.req(m,p,b) if b is not None else w.req(m,p))
        if c in (429,-1): time.sleep(2+a*2); continue
        return c,d
    return c,d
def reqstatus(rid):
    c,d=rq("GET",f"/v2/providers/fms/apis/api/v1/vendors/{w.VID}/requested/{rid}")
    return (d.get("data",{}) or {}).get("content",{}) if isinstance(d,dict) else {}
items=json.load(open("/tmp/overpriced.json"))
plans=[{"vid":it["vid"],"disc":it["raise"],"name":it["name"],"cname":f"FX{it['vid']}"} for it in items if it["raise"]>=10]
print(f"{'[APPLY]' if APPLY else '[DRY]'} 쿠폰 생성대상 {len(plans)}개", flush=True)
for p in plans[:99]: print(f"  {p['name'][:34]:34} 쿠폰 -{p['disc']:,} (이름 {p['cname']})", flush=True)
if not APPLY:
    print("적용하려면 --apply"); sys.exit()
# 1) 생성
for p in plans:
    body={"contractId":CONTRACT,"name":p["cname"],"discount":p["disc"],"type":"PRICE","maxDiscountPrice":p["disc"],"startAt":START,"endAt":END,"wowExclusive":False}
    c,d=rq("POST",f"/v2/providers/fms/apis/api/v2/vendors/{w.VID}/coupon",body)
    p["crid"]=((d.get("data",{}) or {}).get("content",{}) or {}).get("requestedId") if isinstance(d,dict) else None
    if not p["crid"]: p["err"]="생성실패:"+str(d)[:80]
    time.sleep(0.4)
print(f"[1/3] 생성 시도, 실패 {sum(1 for p in plans if p.get('err'))}", flush=True)
# 2) couponId 확보
for _ in range(12):
    pend=[p for p in plans if p.get("crid") and not p.get("cid") and not p.get("err")]
    if not pend: break
    for p in pend:
        st=reqstatus(p["crid"])
        if st.get("status")=="DONE": p["cid"]=st.get("couponId")
        elif st.get("status")=="FAIL": p["err"]="생성FAIL"
    time.sleep(3)
# 3) 연결
for p in plans:
    if not p.get("cid"): continue
    c,d=rq("POST",f"/v2/providers/fms/apis/api/v1/vendors/{w.VID}/coupons/{p['cid']}/items",{"vendorItems":[p["vid"]]})
    p["arid"]=((d.get("data",{}) or {}).get("content",{}) or {}).get("requestedId") if isinstance(d,dict) else None
    time.sleep(0.4)
for _ in range(14):
    pend=[p for p in plans if p.get("arid") and not p.get("done") and not p.get("err")]
    if not pend: break
    for p in pend:
        st=reqstatus(p["arid"])
        if st.get("status")=="DONE" and (st.get("succeeded",0) or 0)>=1: p["done"]=True
        elif st.get("status")=="FAIL": p["err"]="연결FAIL"
    time.sleep(4)
done=[p for p in plans if p.get("done")]; err=[p for p in plans if p.get("err")]
json.dump([{k:p.get(k) for k in('vid','disc','name','cname','cid','done','err')} for p in plans],open("/tmp/fx_result.json","w"),ensure_ascii=False)
print(f"\n[완료] 쿠폰부착 성공 {len(done)} / 실패 {len(err)} / 전체 {len(plans)}", flush=True)
for p in err[:40]: print(f"  ❌ {p['name'][:30]} {p.get('err')}", flush=True)
