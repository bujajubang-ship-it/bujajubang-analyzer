#!/usr/bin/env python3
# 고가(high_value_vids.json) 옵션 재고 체크 → 3개 이하면 5로 복원. 10분 크론.
import wing_server as w
import time, json, datetime, os
HERE="/home/ubuntu/wing"
try: vids=json.load(open(os.path.join(HERE,"high_value_vids.json")))
except Exception as e: print("목록없음",e); raise SystemExit
bumped=[]
for vid in vids:
    c,d=w.req("GET",f"/v2/providers/seller_api/apis/api/v1/marketplace/vendor-items/{vid}/inventories")
    if c!=200 or not isinstance(d,dict): continue
    q=(d.get("data") or {}).get("amountInStock")
    if isinstance(q,int) and q<=3:
        for _ in range(3):
            c2,_=w.req("PUT",f"/v2/providers/seller_api/apis/api/v1/marketplace/vendor-items/{vid}/quantities/5")
            if c2==200: bumped.append((vid,q)); break
            if c2==429: time.sleep(2); continue
            break
    time.sleep(0.05)
if bumped:
    stamp=datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"{stamp} 고가재고 복원 {len(bumped)}건: "+", ".join(f"{v}({q}→5)" for v,q in bumped), flush=True)
