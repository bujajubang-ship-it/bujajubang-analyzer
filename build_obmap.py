import wing_server as w
import time, json
have=set()  # vid with our OB coupon applied
prefcnt={}
for st in ["APPLIED"]:
    for pg in range(1,80):
        c,d=w.req("GET",f"/v2/providers/fms/apis/api/v2/vendors/{w.VID}/coupons?status={st}&page={pg}&size=50")
        cont=(d.get("data",{}) or {}).get("content",[]) if isinstance(d,dict) else []
        if not cont: break
        for x in cont:
            nm=str(x.get("promotionName",""))
            prefcnt[nm[:2]]=prefcnt.get(nm[:2],0)+1
            if nm.startswith("OB") and "_" in nm:
                try: have.add(int(nm.split("_")[1]))
                except: pass
        time.sleep(0.03)
json.dump(sorted(have),open("/home/ubuntu/wing/ob_have_vids.json","w"))
print("APPLIED 쿠폰 접두분포:",prefcnt)
print("OB쿠폰 보유 vid 수:",len(have))
