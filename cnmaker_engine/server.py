"""CN메이커 작업 서버 (Lightsail) — Render가 호출. 비동기 작업 큐."""
import os, json, threading, time, traceback, subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import pipeline
import gptmaker  # CN인사이더 신방식(gpt-image)

BASE = os.path.dirname(os.path.abspath(__file__))
JOBS = {}   # job_id -> {status, msg, result_url, product_name, error}
SECRET = (os.getenv("CNMAKER_SECRET") or pipeline.ENV.get("CNMAKER_SECRET") or "").strip()
INGEST_SECRET = (os.getenv("JAGEUM_INGEST_SECRET") or pipeline.ENV.get("JAGEUM_INGEST_SECRET") or "").strip()
RESULT_DIR = os.path.join(BASE, "results")
os.makedirs(RESULT_DIR, exist_ok=True)
HISTORY_FILE = os.path.join(BASE, "history.json")

def _save_history(job_id, product_name, has_thumb, url="", src=""):
    """완료된 생성을 히스토리에 누적 (최신순, 최대 200개)."""
    try:
        hist = json.load(open(HISTORY_FILE, encoding="utf-8")) if os.path.exists(HISTORY_FILE) else []
    except Exception:
        hist = []
    hist = [h for h in hist if h.get("job") != job_id]  # 중복 제거
    hist.insert(0, {"job": job_id, "product_name": product_name or "(제목없음)",
                    "thumb": bool(has_thumb), "url": url, "src": src,
                    "ts": time.strftime("%Y-%m-%d %H:%M")})
    hist = hist[:200]
    json.dump(hist, open(HISTORY_FILE, "w", encoding="utf-8"), ensure_ascii=False)

# ===== 영업결산: 이카운트 판매조회 온디맨드 재수집 (영업직원이 판매 넘긴 후 새로고침) =====
# 실시간 대신 필요할 때만. 중복 실행 잠금으로 서버 과부하 방지.
SCRAPE_SALES = {"running": False, "msg": "대기", "done_ts": "", "started": 0}

def _scrape_sales_job():
    SCRAPE_SALES.update(running=True, started=time.time(), msg="이카운트 판매조회 수집 중…", done_ts="")
    try:
        p = subprocess.run(
            "cd /home/ubuntu/ecount && python3 sales_scrape.py && python3 build_data.py",
            shell=True, capture_output=True, text=True, timeout=600)
        if p.returncode == 0:
            subprocess.run(
                "cd /home/ubuntu/ecount && curl -s -X POST "
                f"-H 'x-ingest-secret: {INGEST_SECRET}' -H 'Content-Type: application/json' "
                "--data-binary @data/dashboard.json "
                "https://bujajubang-analyzer.onrender.com/jageum/api/ingest",
                shell=True, timeout=90)
            SCRAPE_SALES.update(msg="완료")
        else:
            SCRAPE_SALES.update(msg="수집 실패 (다시 시도해 주세요)")
            print("[scrape_sales] fail rc=%s\n%s" % (p.returncode, (p.stderr or "")[-500:]), flush=True)
    except Exception as e:
        SCRAPE_SALES.update(msg="실패: " + str(e)[:120])
        traceback.print_exc()
    finally:
        SCRAPE_SALES.update(running=False, done_ts=time.strftime("%H:%M:%S"))

# ===== 자금일보 전체 온디맨드 재수집 (금액 안 땡겨왔을 때 대시보드 새로고침 버튼) =====
SCRAPE_JAGEUM = {"running": False, "msg": "대기", "done_ts": "", "started": 0}

def _scrape_jageum_job():
    SCRAPE_JAGEUM.update(running=True, started=time.time(), msg="이카운트 자금일보 수집 중… (2~4분)", done_ts="")
    try:
        # run_daily.sh = 자금일보+손익+판매+미수+정산 전체 수집 후 Render로 ingest까지 수행
        p = subprocess.run("bash /home/ubuntu/ecount/run_daily.sh",
                           shell=True, capture_output=True, text=True, timeout=1200)
        if p.returncode == 0:
            SCRAPE_JAGEUM.update(msg="완료 — 데이터 새로고침 됐어요")
        else:
            SCRAPE_JAGEUM.update(msg="수집 실패 (잠시 후 다시 시도해 주세요)")
            print("[scrape_jageum] fail rc=%s\n%s" % (p.returncode, (p.stderr or "")[-500:]), flush=True)
    except Exception as e:
        SCRAPE_JAGEUM.update(msg="실패: " + str(e)[:120])
        traceback.print_exc()
    finally:
        SCRAPE_JAGEUM.update(running=False, done_ts=time.strftime("%H:%M:%S"))

SCRAPE_PLPROJ = {"running": False, "msg": "대기", "done_ts": "", "started": 0}

def _scrape_plproj_job():
    SCRAPE_PLPROJ.update(running=True, started=time.time(), msg="이카운트 프로젝트별 손익 수집 중… (5~7분)", done_ts="")
    try:
        p = subprocess.run("python3 /home/ubuntu/ecount/pl_proj.py",
                           shell=True, capture_output=True, text=True, timeout=1800)
        if p.returncode == 0:
            SCRAPE_PLPROJ.update(msg="완료 — 팀별 손익 갱신됐어요")
        else:
            SCRAPE_PLPROJ.update(msg="수집 실패 (잠시 후 다시 시도해 주세요)")
            print("[scrape_plproj] fail rc=%s\n%s" % (p.returncode, (p.stderr or "")[-500:]), flush=True)
    except Exception as e:
        SCRAPE_PLPROJ.update(msg="실패: " + str(e)[:120])
        traceback.print_exc()
    finally:
        SCRAPE_PLPROJ.update(running=False, done_ts=time.strftime("%H:%M:%S"))

def worker(job_id, url, category='kitchen'):
    JOBS[job_id]={"status":"running","msg":"시작"}
    try:
        out=os.path.join(RESULT_DIR, job_id+".jpg")
        def patched_log(m):
            JOBS[job_id]["msg"]=m
            print(f"[{job_id}] {m}", flush=True)
        # CN인사이더·카페24(국내) = 신방식(gpt-image), 그 외 = 기존 pipeline
        src=pipeline.detect_source(gptmaker.normalize_url(url))
        if src in ("cninsider","cafe24"):
            gptmaker.log=patched_log; gptmaker.P.log=patched_log
            r=gptmaker.run(url, out, category=category)
            thumb=r.get("thumb")
            has_thumb=bool(thumb and os.path.exists(thumb))
            JOBS[job_id]={"status":"done","product_name":r["product_name"],
                          "result":job_id+".jpg",
                          "thumb":(job_id+"_thumb.jpg" if has_thumb else None),
                          "copy":r["copy"],"msg":"완료"}
            _save_history(job_id, r["product_name"], has_thumb, url, src)
        else:
            pipeline.log=patched_log
            r=pipeline.run(url, out, category=category)
            JOBS[job_id]={"status":"done","product_name":r["product_name"],
                          "result":job_id+".jpg","copy":r["copy"],"msg":"완료"}
            _save_history(job_id, r["product_name"], False, url, src)
    except Exception as e:
        traceback.print_exc()
        JOBS[job_id]={"status":"error","error":str(e)[:200],"msg":"실패"}

def worker_imgs(job_id, image_paths, title, category='kitchen'):
    JOBS[job_id]={"status":"running","msg":"시작"}
    try:
        out=os.path.join(RESULT_DIR, job_id+".jpg")
        def pl(m):
            JOBS[job_id]["msg"]=m; print("["+job_id+"] "+m, flush=True)
        pipeline.log=pl
        r=pipeline.run_from_images(image_paths, title, out, category=category)
        JOBS[job_id]={"status":"done","product_name":r["product_name"],
                      "result":job_id+".jpg","copy":r["copy"],"msg":"완료"}
        _save_history(job_id, r["product_name"], False, "", "이미지업로드")
    except Exception as e:
        traceback.print_exc()
        JOBS[job_id]={"status":"error","error":str(e)[:200],"msg":"실패"}


def analyze_product(url):
    """이미지 생성 없이 CN인사이더 상품명과 기준 이미지 주소만 수집한다."""
    normalized = gptmaker.normalize_url((url or "").strip())
    if not normalized.startswith("http"):
        raise ValueError("1688 상품 링크가 필요합니다")
    if pipeline.detect_source(normalized) != "cninsider":
        raise ValueError("CN인사이더 상품 링크를 확인해 주세요")
    data = gptmaker.login_and_scrape(normalized)
    images = [src for src in data.get("main_imgs", []) if str(src).startswith("http")][:10]
    if not images:
        raise RuntimeError("상품 이미지를 찾지 못했습니다")
    return {"title": (data.get("title") or "").strip(), "images": images}


def worker_plan_draft(job_id, plan, image_paths, reference_urls):
    JOBS[job_id] = {"status": "running", "msg": "저해상도 시안 준비"}
    try:
        out = os.path.join(RESULT_DIR, job_id + ".jpg")
        def patched_log(message):
            JOBS[job_id]["msg"] = message
            print(f"[{job_id}] {message}", flush=True)
        gptmaker.log = patched_log
        result = gptmaker.run_plan_draft(plan, image_paths, reference_urls, out)
        JOBS[job_id] = {
            "status": "done", "msg": "저해상도 시안 완성",
            "product_name": result["product_name"], "result": job_id + ".jpg",
            "copy": {"headline": "텍스트 기획안 확정본 사용"},
            "draft": True, "section_count": result["section_count"],
        }
    except Exception as e:
        traceback.print_exc()
        JOBS[job_id] = {"status": "error", "error": str(e)[:200], "msg": "시안 생성 실패"}

class H(BaseHTTPRequestHandler):
    def log_message(self,*a): pass
    def _send(self,code,obj,ctype="application/json"):
        self.send_response(code); self.send_header("Content-Type",ctype)
        self.send_header("Access-Control-Allow-Origin","*"); self.end_headers()
        if isinstance(obj,bytes): self.wfile.write(obj)
        else: self.wfile.write(json.dumps(obj,ensure_ascii=False).encode())
    def _transcribe(self):
        # youtube-researcher가 오디오만 보내면 여기서 cn.env OpenAI 키로 받아쓰기 (키는 Lightsail 밖으로 안 나감)
        import urllib.request, urllib.error
        ln=int(self.headers.get("Content-Length",0))
        audio=self.rfile.read(ln)
        if not audio: return self._send(400,{"error":"no audio"})
        key=getattr(gptmaker,"OKEY","") or os.environ.get("OPENAI_API_KEY","")
        if not key: return self._send(500,{"error":"no openai key"})
        bnd="----tx"+str(int(time.time()*1000))
        pre=(f"--{bnd}\r\nContent-Disposition: form-data; name=\"model\"\r\n\r\nwhisper-1\r\n"
             f"--{bnd}\r\nContent-Disposition: form-data; name=\"language\"\r\n\r\nko\r\n"
             f"--{bnd}\r\nContent-Disposition: form-data; name=\"response_format\"\r\n\r\nverbose_json\r\n"
             f"--{bnd}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"a.mp3\"\r\nContent-Type: audio/mpeg\r\n\r\n").encode()
        body=pre+audio+(f"\r\n--{bnd}--\r\n").encode()
        req=urllib.request.Request("https://api.openai.com/v1/audio/transcriptions", data=body,
            headers={"Authorization":"Bearer "+key,"Content-Type":"multipart/form-data; boundary="+bnd})
        try:
            d=json.loads(urllib.request.urlopen(req,timeout=600).read())
            return self._send(200,{"ok":True,"text":d.get("text",""),"segments":d.get("segments",[])})
        except urllib.error.HTTPError as e:
            return self._send(502,{"error":"openai %d: %s"%(e.code, e.read().decode()[:200])})
        except Exception as e:
            return self._send(500,{"error":str(e)[:200]})

    def do_POST(self):
        if self.headers.get("x-secret")!=SECRET: return self._send(403,{"error":"forbidden"})
        if self.path=="/transcribe": return self._transcribe()
        ln=int(self.headers.get("Content-Length",0)); body=json.loads(self.rfile.read(ln) or "{}")
        if self.path.startswith("/kv/"):
            key="".join(c for c in self.path[4:].split("?")[0] if c.isalnum() or c in "_-")
            if not key: return self._send(400,{"error":"bad key"})
            kvdir=os.path.join(BASE,"kv"); os.makedirs(kvdir,exist_ok=True)
            json.dump(body, open(os.path.join(kvdir,key+".json"),"w",encoding="utf-8"), ensure_ascii=False)
            return self._send(200,{"ok":True})
        if self.path=="/scrape_sales":
            # 이미 수집 중이면 중복 실행 금지(과부하 방지)
            if SCRAPE_SALES["running"] and (time.time()-SCRAPE_SALES["started"] < 600):
                return self._send(200,{"status":"busy","msg":SCRAPE_SALES["msg"]})
            threading.Thread(target=_scrape_sales_job, daemon=True).start()
            return self._send(200,{"status":"started"})
        if self.path=="/scrape_jageum":
            # 이미 수집 중이면 중복 실행 금지 (전체 수집은 무거워서 20분 잠금)
            if SCRAPE_JAGEUM["running"] and (time.time()-SCRAPE_JAGEUM["started"] < 1200):
                return self._send(200,{"status":"busy","msg":SCRAPE_JAGEUM["msg"]})
            threading.Thread(target=_scrape_jageum_job, daemon=True).start()
            return self._send(200,{"status":"started"})
        if self.path=="/scrape_pl_proj":
            if SCRAPE_PLPROJ["running"] and (time.time()-SCRAPE_PLPROJ["started"] < 1800):
                return self._send(200,{"status":"busy","msg":SCRAPE_PLPROJ["msg"]})
            threading.Thread(target=_scrape_plproj_job, daemon=True).start()
            return self._send(200,{"status":"started"})
        if self.path=="/cnmaker/start":
            url=(body.get("url") or "").strip(); cat=(body.get("category") or "kitchen").strip()
            if not url.startswith("http"): return self._send(400,{"error":"URL 필요"})
            import uuid; jid=uuid.uuid4().hex[:12]
            threading.Thread(target=worker,args=(jid,url,cat),daemon=True).start()
            return self._send(200,{"job_id":jid})
        if self.path=="/cnmaker/analyze":
            url=(body.get("url") or "").strip()
            try:
                return self._send(200,{"ok":True,"product":analyze_product(url)})
            except ValueError as e:
                return self._send(400,{"error":str(e)})
            except Exception as e:
                print("[cnmaker/analyze] "+str(e)[:200], flush=True)
                return self._send(502,{"error":"1688 상품정보를 가져오지 못했습니다. 링크와 로그인을 확인해 주세요."})
        if self.path=="/cnmaker/start_plan_draft":
            import uuid, base64
            project_id=(body.get("project_id") or "").strip()
            plan=body.get("plan") or {}; images=body.get("images") or []
            reference_urls=[url for url in (body.get("reference_urls") or []) if str(url).startswith("http")][:10]
            if len(project_id)!=12 or any(char not in "0123456789abcdef" for char in project_id) or len(plan.get("sections") or []) != 11:
                return self._send(400,{"error":"확정된 기획안을 확인해 주세요"})
            if len(images)>10:
                return self._send(400,{"error":"기준 이미지는 최대 10장입니다"})
            jid=uuid.uuid4().hex[:12]; paths=[]
            updir=os.path.join(RESULT_DIR,"up",project_id); os.makedirs(updir,exist_ok=True)
            try:
                for i,value in enumerate(images):
                    encoded=value.split(",",1)[1] if "," in value else value
                    raw=base64.b64decode(encoded,validate=True)
                    if len(raw)>5*1024*1024: return self._send(413,{"error":"이미지 한 장은 5MB 이하여야 합니다"})
                    path=os.path.join(updir,str(i)+".jpg"); open(path,"wb").write(raw); paths.append(path)
            except Exception:
                return self._send(400,{"error":"기준 이미지를 읽지 못했습니다"})
            threading.Thread(target=worker_plan_draft,args=(jid,plan,paths,reference_urls),daemon=True).start()
            return self._send(200,{"job_id":jid})
        if self.path=="/cnmaker/start_imgs":
            import uuid, base64; jid=uuid.uuid4().hex[:12]
            imgs=body.get("images",[]); title=(body.get("title") or "").strip(); cat=(body.get("category") or "kitchen").strip()
            paths=[]
            updir=os.path.join(RESULT_DIR,"up"); os.makedirs(updir,exist_ok=True)
            for i,b64 in enumerate(imgs[:8]):
                if "," in b64: b64=b64.split(",",1)[1]
                raw=base64.b64decode(b64)
                fp=os.path.join(updir, jid+"_"+str(i)+".img"); open(fp,"wb").write(raw); paths.append(fp)
            if not paths: return self._send(400,{"error":"이미지 필요"})
            threading.Thread(target=worker_imgs,args=(jid,paths,title,cat),daemon=True).start()
            return self._send(200,{"job_id":jid})
        self._send(404,{"error":"unknown"})
    def do_GET(self):
        # /cnmaker/status?job=xxx  /cnmaker/result?job=xxx
        from urllib.parse import urlparse, parse_qs
        q=parse_qs(urlparse(self.path).query); jid=(q.get("job",[""])[0])
        if self.path.startswith("/kv/"):
            if self.headers.get("x-secret")!=SECRET: return self._send(403,{"error":"forbidden"})
            key="".join(c for c in urlparse(self.path).path[4:] if c.isalnum() or c in "_-")
            fp=os.path.join(BASE,"kv",key+".json")
            if os.path.exists(fp): return self._send(200,{"ok":True,"data":json.load(open(fp,encoding="utf-8"))})
            return self._send(200,{"ok":True,"data":None})
        if self.path.startswith("/scrape_sales_status"):
            if self.headers.get("x-secret")!=SECRET: return self._send(403,{"error":"forbidden"})
            return self._send(200,{k:SCRAPE_SALES[k] for k in ("running","msg","done_ts")})
        if self.path.startswith("/scrape_jageum_status"):
            if self.headers.get("x-secret")!=SECRET: return self._send(403,{"error":"forbidden"})
            return self._send(200,{k:SCRAPE_JAGEUM[k] for k in ("running","msg","done_ts")})
        if self.path.startswith("/scrape_pl_proj_status"):
            if self.headers.get("x-secret")!=SECRET: return self._send(403,{"error":"forbidden"})
            return self._send(200,{k:SCRAPE_PLPROJ[k] for k in ("running","msg","done_ts")})
        if self.path.startswith("/cnmaker/status"):
            if self.headers.get("x-secret")!=SECRET: return self._send(403,{"error":"forbidden"})
            return self._send(200, JOBS.get(jid,{"status":"unknown"}))
        if self.path.startswith("/cnmaker/history"):
            if self.headers.get("x-secret")!=SECRET: return self._send(403,{"error":"forbidden"})
            try: hist=json.load(open(HISTORY_FILE,encoding="utf-8")) if os.path.exists(HISTORY_FILE) else []
            except Exception: hist=[]
            # 결과 파일이 실제 존재하는 것만
            hist=[h for h in hist if os.path.exists(os.path.join(RESULT_DIR,h["job"]+".jpg"))]
            return self._send(200,{"items":hist})
        if self.path.startswith("/cnmaker/result"):
            is_thumb=(q.get("thumb",[""])[0])
            fn=jid+"_thumb.jpg" if is_thumb else jid+".jpg"
            fp=os.path.join(RESULT_DIR, fn)
            if os.path.exists(fp):
                return self._send(200, open(fp,"rb").read(), "image/jpeg")
            return self._send(404,{"error":"no result"})
        self._send(404,{"error":"unknown"})

if __name__=="__main__":
    port=int(os.environ.get("PORT","8090"))
    print(f"cnmaker server on {port}", flush=True)
    ThreadingHTTPServer(("0.0.0.0",port),H).serve_forever()
