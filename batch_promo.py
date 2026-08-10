"""쿠팡 판매상품 일괄 프로모션 배치 (정가↑+쿠폰+재고9). 안전: 쿠폰 적용확인 후에만 가격 인상.
사용: python3 batch_promo.py <START> <LIMIT>   (예: 0 100)
"""
import datetime, hmac, hashlib, httpx, asyncio, json, os, random, sys
os.chdir("/Users/gun/coupang-analyzer")
START=int(sys.argv[1]) if len(sys.argv)>1 else 0
LIMIT=int(sys.argv[2]) if len(sys.argv)>2 else 100
env={}
for line in open('.env',encoding='utf-8'):
    line=line.strip()
    if '=' in line and not line.startswith('#'): k,v=line.split('=',1); env[k]=v
AK=env['COUPANG_WING_ACCESS_KEY']; SK=env['COUPANG_WING_SECRET_KEY']; VID=env['COUPANG_WING_VENDOR_ID']
BASE="https://api-gateway.coupang.com"; CONTRACT=276433
def auth(m,pq):
    p=pq.split("?",1); path=p[0]; q=p[1] if len(p)>1 else ""
    dt=datetime.datetime.utcnow(); sd=dt.strftime("%y%m%d")+"T"+dt.strftime("%H%M%S")+"Z"
    return f"CEA algorithm=HmacSHA256, access-key={AK}, signed-date={sd}, signature={hmac.new(SK.encode(),(sd+m+path+q).encode(),hashlib.sha256).hexdigest()}"
async def req(m,pq,body=None,retry=2):
    for a in range(retry+1):
        try:
            async with httpx.AsyncClient(timeout=40) as c:
                h={"Authorization":auth(m,pq),"Content-Type":"application/json;charset=UTF-8"}
                if m=="GET": r=await c.get(BASE+pq,headers=h)
                else: r=await c.request(m,BASE+pq,headers=h,content=(json.dumps(body,ensure_ascii=False).encode() if body is not None else None))
                if r.status_code==429: await asyncio.sleep(3); continue
                try: return r.status_code,r.json()
                except: return r.status_code,r.text
        except Exception as e:
            if a==retry: return -1,str(e)
            await asyncio.sleep(2)
def log(m): print(m,flush=True)
def natural(n):
    n=int(round(n))
    if n>=10000: n=round(n/100)*100-10
    elif n>=1000: n=round(n/10)*10-10
    return max(n,100)
async def attach_done(rid):
    code,d=await req("GET",f"/v2/providers/fms/apis/api/v1/vendors/{VID}/requested/{rid}")
    cont=(d.get("data",{}) or {}).get("content",{}) if isinstance(d,dict) else {}
    if not isinstance(cont,dict): return None
    if cont.get("status")=="DONE": return (cont.get("succeeded",0) or 0)>=1
    if cont.get("status")=="FAIL": return False
    return None

async def main():
    from collections import defaultdict
    # 판매량 집계
    qty=defaultdict(int); names={}; coup=set()
    today=datetime.date(2026,6,24)
    for wk in range(5):
        end=today-datetime.timedelta(days=wk*7); start=end-datetime.timedelta(days=6)
        for st in ["FINAL_DELIVERY","DELIVERING","DEPARTURE","INSTRUCT","ACCEPT"]:
            code,d=await req("GET",f"/v2/providers/openapi/apis/api/v4/vendors/{VID}/ordersheets?createdAtFrom={start}&createdAtTo={end}&status={st}&maxPerPage=50")
            if code==200 and isinstance(d,dict):
                for box in d.get("data",[]):
                    for oi in box.get("orderItems",[]):
                        sid=oi.get("sellerProductId"); names[sid]=oi.get("sellerProductName","")
                        qty[sid]+=oi.get("shippingCount",0) or 0
                        if (oi.get("instantCouponDiscount",0) or 0)>0: coup.add(sid)
    # 전체 승인상품
    allp=[]; token=""
    for _ in range(40):
        code,d=await req("GET",f"/v2/providers/seller_api/apis/api/v1/marketplace/seller-products?vendorId={VID}&maxPerPage=50"+(f"&nextToken={token}" if token else ""))
        if code!=200: break
        for p in d.get("data",[]):
            if p.get("statusName")=="승인완료":
                allp.append(p["sellerProductId"]); names.setdefault(p["sellerProductId"],p.get("sellerProductName",""))
        token=d.get("nextToken","")
        if not token: break
    # 랭킹: 판매순 → 나머지(sid 내림차순)
    sold=[s for s,_ in sorted(qty.items(),key=lambda x:-x[1])]
    rest=[s for s in sorted(set(allp),reverse=True) if s not in qty]
    ranked=[s for s in sold+rest]
    cand=ranked[START:START+LIMIT]
    log(f"=== 배치 START={START} LIMIT={LIMIT} → 후보 {len(cand)}개 (전체 승인 {len(allp)}) ===")
    # 상세조회 + 계획
    rng=random.Random(1000+START)
    plans=[]
    for sid in cand:
        code,d=await req("GET",f"/v2/providers/seller_api/apis/api/v1/marketplace/seller-products/{sid}")
        if code!=200: continue
        it=next((x for x in d.get("data",{}).get("items",[]) if x.get("vendorItemId")),None)
        if not it: continue
        price=it.get("salePrice",0) or 0
        if price<5000: continue
        pct=rng.uniform(0.025,0.035) if price>1000000 else rng.uniform(0.08,0.12)
        new=natural(price*(1+pct)); disc=new-price
        if disc<10: continue
        plans.append({"sid":sid,"vid":it["vendorItemId"],"name":names.get(sid,""),"price":price,"new":new,"disc":disc,
                      "cname":f"BJ{START}_{len(plans)}","cid":None,"rid":None,"done":False,"skip":None})
        await asyncio.sleep(0.15)
    log(f"적용 대상 {len(plans)}개 (저가/옵션없음 제외 후)")
    # 1) 쿠폰 생성
    log("[1/4] 쿠폰 생성")
    for p in plans:
        body={"contractId":CONTRACT,"name":p["cname"],"discount":p["disc"],"type":"PRICE","maxDiscountPrice":p["disc"],
              "startAt":"2026-06-24 00:00:00","endAt":"2027-06-24 23:59:00","wowExclusive":False}
        code,d=await req("POST",f"/v2/providers/fms/apis/api/v2/vendors/{VID}/coupon",body)
        if not (isinstance(d,dict) and d.get("data",{}).get("success")):
            p["skip"]="쿠폰생성실패"; log(f"  FAIL 생성 {p['name'][:18]} {str(d)[:80]}")
        await asyncio.sleep(0.4)
    # 2) couponId 매핑 (페이지 순회)
    await asyncio.sleep(15)
    name2id={}
    for st in ["WAIT_APPLY","APPLIED"]:
        for pg in range(1,8):
            code,d=await req("GET",f"/v2/providers/fms/apis/api/v2/vendors/{VID}/coupons?status={st}&page={pg}&size=50")
            cont=(d.get("data",{}) or {}).get("content",[]) if isinstance(d,dict) else []
            if not cont: break
            for c in cont:
                if str(c.get("promotionName","")).startswith(f"BJ{START}_"): name2id[c["promotionName"]]=c["couponId"]
    for p in plans:
        if not p["skip"]: p["cid"]=name2id.get(p["cname"])
    # 3) 옵션 연결
    log("[2/4] 옵션 연결")
    for p in plans:
        if p["skip"] or not p["cid"]: continue
        code,d=await req("POST",f"/v2/providers/fms/apis/api/v1/vendors/{VID}/coupons/{p['cid']}/items",{"vendorItems":[p["vid"]]})
        p["rid"]=((d.get("data",{}) or {}).get("content",{}) or {}).get("requestedId") if isinstance(d,dict) else None
        await asyncio.sleep(0.35)
    # 4) 적용확인 → 가격/재고
    log("[3/4] 적용확인 → 가격/재고")
    for t in range(18):
        pend=[p for p in plans if p["cid"] and p["rid"] and p["done"] is False and p["skip"] is None]
        if not pend: break
        for p in pend:
            r=await attach_done(p["rid"])
            if r is True:
                c1,_=await req("PUT",f"/v2/providers/seller_api/apis/api/v1/marketplace/vendor-items/{p['vid']}/prices/{p['new']}")
                c2,_=await req("PUT",f"/v2/providers/seller_api/apis/api/v1/marketplace/vendor-items/{p['vid']}/quantities/9")
                p["done"]=(c1==200 and c2==200)
            elif r is False:
                p["skip"]="기존쿠폰/적용실패(CIR08등)"
            await asyncio.sleep(0.2)
        await asyncio.sleep(35)
    # 결과
    done=[p for p in plans if p["done"]]; skip=[p for p in plans if p["skip"]]
    json.dump([{k:p[k] for k in('sid','name','price','new','disc','done','skip')} for p in plans],
              open(f'/tmp/batch_{START}.json','w'),ensure_ascii=False)
    log(f"[4/4] 완료: 적용 {len(done)} / 스킵 {len(skip)} / 전체 {len(plans)}")
    for p in done[:50]: log(f"  ✅ {p['name'][:30]} {p['price']:,}→{p['new']:,} -{p['disc']:,}")
    for p in skip[:20]: log(f"  ⏭️ {p['name'][:26]} ({p['skip']})")
    log(f"BATCH_DONE START={START} 다음START={START+LIMIT}")
asyncio.run(main())
