import hmac
import hashlib
import datetime
import requests

ACCESS_KEY = "fb4d5823-5f7f-4391-ac80-872896363b86"
SECRET_KEY = "efe6d08ace59321d6349c33eaa2c437c1c560dc6"
BASE_URL = "https://api-gateway.coupang.com"

def generate_auth(method, url, secret_key, access_key):
    """Java SDK와 동일한 로직: path + query (? 제외)"""
    parts = url.split("?", 1)
    path = parts[0]
    query = parts[1] if len(parts) > 1 else ""

    dt = datetime.datetime.utcnow()
    signed_date = dt.strftime("%y%m%d") + "T" + dt.strftime("%H%M%S") + "Z"

    # 핵심: path + query (?없이 붙임)
    message = signed_date + method + path + query
    print(f"[DEBUG] message = {repr(message)}")

    sig = hmac.new(secret_key.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"CEA algorithm=HmacSHA256, access-key={access_key}, signed-date={signed_date}, signature={sig}"

url = "/v2/providers/affiliate_open_api/apis/openapi/products/search?keyword=refrigerator&limit=3"
auth = generate_auth("GET", url, SECRET_KEY, ACCESS_KEY)

r = requests.get(BASE_URL + url, headers={
    "Authorization": auth,
    "Content-Type": "application/json;charset=UTF-8",
})
print(f"\nStatus: {r.status_code}")
print(r.text[:1000])
