import wing_server as w
import json, time
T={
 16191781732:"스타리온 1등급 직냉식 업소용 냉장고 냉동고 25박스 45박스 65박스",
 16191779945:"스타리온 1등급 간냉식 업소용냉장고 25박스 45박스",
 15427509512:"유니크 대성 업소용 올스텐 영업용 숙성고 25박스 30박스 45박스 1도어 2도어",
 15427509455:"스타리온 2세대 스탠드형 업소용냉장고 25박스 35박스 45박스",
}
for sid,newt in T.items():
    c,d=w.req("GET",f"/v2/providers/seller_api/apis/api/v1/marketplace/seller-products/{sid}")
    data=d.get("data",{}) if isinstance(d,dict) else {}
    if not data: print(f"[{sid}] GET실패"); continue
    old=data.get("sellerProductName")
    data["sellerProductName"]=newt
    c2,d2=w.req("PUT","/v2/providers/seller_api/apis/api/v1/marketplace/seller-products",data)
    ok=isinstance(d2,dict) and d2.get("code")=="SUCCESS"
    time.sleep(2)
    if ok:
        c3,d3=w.req("PUT",f"/v2/providers/seller_api/apis/api/v1/marketplace/seller-products/{sid}/approvals")
        time.sleep(3)
    c4,d4=w.req("GET",f"/v2/providers/seller_api/apis/api/v1/marketplace/seller-products/{sid}")
    dd=d4.get("data",{}) if isinstance(d4,dict) else {}
    print(f"[{sid}] PUT {'OK' if ok else d2} | 승인요청 {'OK' if ok else '-'} | 현재상태 {dd.get('statusName')} | 제목 {dd.get('sellerProductName','')[:50]}")
