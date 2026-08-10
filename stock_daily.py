#!/usr/bin/env python3
# 매일 baseline 리셋: 옵션 판매가>=500000 → 5, 그외 → 9. 06:00 크론. 고가목록도 갱신.
import wing_server as w
import time, json, datetime
THRESH=500000
def put(vid,q):
    for _ in range(4):
        c,_=w.req("PUT",f"/v2/providers/seller_api/apis/api/v1/marketplace/vendor-items/{vid}/quantities/{q}")
        if c==200: return True
        if c==429: time.sleep(2); continue
        return False
    return False
items=w.load_items(); hv=[]; fix9=0; fix5=0; fail=0
for it in items:
    vid=it["vid"]
    c,d=w.req("GET",f"/v2/providers/seller_api/apis/api/v1/marketplace/vendor-items/{vid}/inventories")
    if c!=200 or not isinstance(d,dict): continue
    data=d.get("data") or {}; price=data.get("salePrice"); q=data.get("amountInStock")
    if not isinstance(q,int): continue
    if isinstance(price,int) and price>=THRESH:
        hv.append(vid)
        if q<=3 or q>5:           # 고가: 4~5는 유지, 그 밖이면 5로
            if put(vid,5): fix5+=1
            else: fail+=1
    else:
        if q!=9:
            if put(vid,9): fix9+=1
            else: fail+=1
    time.sleep(0.05)
json.dump(hv,open("high_value_vids.json","w"))
stamp=datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
print(f"{stamp} 일일유지: 일반9복원 {fix9} / 고가5복원 {fix5} / 실패 {fail} / 고가목록 {len(hv)}개", flush=True)
