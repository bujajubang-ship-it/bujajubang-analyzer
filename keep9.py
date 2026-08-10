#!/usr/bin/env python3
# 재고 9 유지: 모든 옵션 조회 → 9가 아니면 9로 복원 (매일 아침 크론).
# wing_server의 헬퍼 재사용. 사용: python3 keep9.py
import wing_server as w
import time, datetime, json

def put9(vid):
    for _ in range(4):
        c, _ = w.req("PUT", f"/v2/providers/seller_api/apis/api/v1/marketplace/vendor-items/{vid}/quantities/9")
        if c == 200: return True
        if c == 429: time.sleep(2); continue
        return False
    return False

def main():
    items = w.load_items()
    low = high = fail = 0; changed = []
    for it in items:
        vid = it["vid"]
        c, d = w.req("GET", f"/v2/providers/seller_api/apis/api/v1/marketplace/vendor-items/{vid}/inventories")
        if c == 200 and isinstance(d, dict):
            q = (d.get("data") or {}).get("amountInStock")
            if isinstance(q, int) and q != 9:
                if put9(vid):
                    if q < 9: low += 1
                    else: high += 1
                    changed.append((it.get("name", "")[:34], q))
                else:
                    fail += 1
        time.sleep(0.05)
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    n = low + high
    print(f"{stamp} 재고9 유지: 복원 {n}건 (9미만 {low} / 9초과 {high}) 실패 {fail} / 총 {len(items)}", flush=True)
    if n:
        body = f"[{stamp}] 재고 9로 복원 {n}건 (9미만 {low} / 9초과 {high})\n\n" + \
               "\n".join(f"- {nm} : {q}→9" for nm, q in changed[:60])
        try: w.send_email(f"[부자홀딩스] 재고 9 복원 {n}건", body)
        except Exception as e: print("메일실패", e, flush=True)

if __name__ == "__main__":
    main()
