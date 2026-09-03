"""이미지 변형 (역검색 방지, A안=자연스럽게)"""
import io, numpy as np
from PIL import Image, ImageEnhance, ImageFilter

def transform(raw: bytes, flip=False, seed=0) -> bytes:
    img=Image.open(io.BytesIO(raw)).convert("RGB")
    rng=np.random.RandomState(seed)
    # 1) 좌우반전(제품 대칭이면 티 안남)
    if flip: img=img.transpose(Image.FLIP_LEFT_RIGHT)
    # 2) 미세 크롭(가장자리 1~3% 잘라 픽셀정렬 깨기)
    w,h=img.size
    cx=int(w*rng.uniform(0.01,0.025)); cy=int(h*rng.uniform(0.01,0.025))
    img=img.crop((cx,cy,w-cx,h-cy)).resize((w,h),Image.LANCZOS)
    # 3) 색/밝기/대비 미세조정(±2~4%)
    img=ImageEnhance.Brightness(img).enhance(rng.uniform(0.97,1.03))
    img=ImageEnhance.Contrast(img).enhance(rng.uniform(0.97,1.03))
    img=ImageEnhance.Color(img).enhance(rng.uniform(0.97,1.04))
    # 4) 약한 노이즈(해시 깨기, 눈엔 안보임)
    arr=np.array(img).astype(np.int16)
    noise=rng.randint(-4,5,arr.shape)
    arr=np.clip(arr+noise,0,255).astype(np.uint8)
    img=Image.fromarray(arr)
    # 5) 아주 약한 샤픈(재인코딩 흔적 자연화)
    img=img.filter(ImageFilter.UnsharpMask(radius=1,percent=30,threshold=2))
    out=io.BytesIO(); img.save(out,"JPEG",quality=88); return out.getvalue()

if __name__=="__main__":
    import urllib.request, json
    data=json.load(open("/home/ubuntu/cn_copy.json"))
    HDR={"User-Agent":"Mozilla/5.0","Referer":"https://www.cninsider.co.kr/"}
    src=data["main_imgs"][0]
    raw=urllib.request.urlopen(urllib.request.Request(src,headers=HDR),timeout=20).read()
    open("/home/ubuntu/orig.jpg","wb").write(raw)
    open("/home/ubuntu/transformed.jpg","wb").write(transform(raw,seed=1))
    print("원본/변형 저장 완료, 원본크기:",len(raw),"변형:",len(transform(raw,seed=1)))
