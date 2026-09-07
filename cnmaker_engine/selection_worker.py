"""Isolated browser worker. No image analysis is performed during collection."""
import json
from pathlib import Path
import sys
import os
import gptmaker as G

if __name__=='__main__':
    url,target=sys.argv[1:]
    os.environ['CN_SELECTION_CATALOGUE']=target
    os.environ['CN_SELECTION_URL']=G.normalize_url(url)
    data=G.login_and_scrape(url,include_details=True) if G.P.detect_source(G.normalize_url(url))=='cninsider' else G.scrape_cafe24(url)
    if 'images' not in data:
        data['images']=[dict(url=u,role=r) for r,k in (('product','main_imgs'),('detail','detail_imgs')) for u in data.get(k,[])]
    Path(target).write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8')
