#!/usr/bin/env python3
"""🏢 경쟁사 분석 — 경쟁사가 쿠팡에서 뭘로 얼마 버는지 본다.

사장님이 경쟁사 판매 화면을 사진으로 찍어오면, 그걸 읽어서 만든 표를 여기에 올린다.
자동 수집이 아니라 사람이 확인한 값이라 숫자를 믿을 수 있고, 대신 자주 갱신되지는 않는다.

업체 한 곳 = 파일 하나(`{이름}.json`). 두 군데를 본다:
  1) 영구 디스크(DATA_DIR/competitors) — 업로드 API로 올린 것
  2) 저장소 안 competitors/ — 처음부터 같이 딸려오는 것
같은 이름이면 영구 디스크가 이긴다(나중에 올린 게 최신이니까).
"""
import json
import os
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse


def _guard(request: Request):
    """소싱 사이트 로그인과 같은 잠금. 업로드는 시크릿으로 따로 본다."""
    from main import _site_auth
    if not _site_auth(request):
        raise HTTPException(status_code=401, detail="로그인이 필요합니다")


router = APIRouter(prefix="/market/api", dependencies=[Depends(_guard)])
upload_router = APIRouter(prefix="/market/api")   # 업로드는 로그인 대신 시크릿으로

DATA_DIR = Path(os.getenv("DATA_DIR", "."))
DISK_DIR = DATA_DIR / "competitors"          # 업로드로 올린 것 (재배포해도 남는다)
REPO_DIR = Path(__file__).parent / "competitors"   # 저장소에 같이 들어 있는 것
UPLOAD_SECRET = os.getenv("MARKET_UPLOAD_SECRET", "bj-market-2026")


def _safe(slug: str) -> str:
    """파일 이름에 경로를 섞어 넣는 장난을 막는다."""
    s = os.path.basename(slug or "").strip()
    if not s or s.startswith(".") or "/" in s or "\\" in s:
        raise HTTPException(status_code=400, detail="이름이 올바르지 않습니다")
    return s[:60]


def _find(slug: str):
    for d in (DISK_DIR, REPO_DIR):
        p = d / f"{slug}.json"
        if p.exists():
            return p
    return None


def _load(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


@router.get("/competitors")
def competitor_list():
    """업체 목록. 화면 왼쪽 목록에 쓴다."""
    seen, out = set(), []
    for d in (DISK_DIR, REPO_DIR):
        if not d.exists():
            continue
        for p in sorted(d.glob("*.json")):
            if p.stem in seen:
                continue
            seen.add(p.stem)
            data = _load(p) or {}
            out.append({
                "slug": p.stem,
                "업체": data.get("업체") or p.stem,
                "분야": data.get("분야") or "",
                "한줄": data.get("한줄") or "",
                "총매출": data.get("총매출") or 0,
                "상품수": len(data.get("상품") or []),
            })
    out.sort(key=lambda x: -x["총매출"])
    return {"items": out}


@router.get("/competitor/{slug}")
def competitor_one(slug: str):
    p = _find(_safe(slug))
    if not p:
        raise HTTPException(status_code=404, detail="그 업체 자료가 아직 없습니다")
    data = _load(p)
    if data is None:
        raise HTTPException(status_code=500, detail="자료를 읽지 못했습니다")
    return data


@upload_router.post("/competitor-upload")
async def competitor_upload(request: Request):
    """경쟁사 자료 올리기. 사람이 확인한 JSON만 들어온다.

    경로에 `-`를 쓴 이유: /competitor/{slug} 가 있으면 /competitor/upload 는
    slug 로 잡혀서 405가 난다(이미 밟은 함정).
    """
    if request.headers.get("x-secret") != UPLOAD_SECRET:
        raise HTTPException(status_code=401, detail="시크릿이 다릅니다")
    body = await request.json()
    slug = _safe(body.get("slug") or body.get("업체") or "")
    data = body.get("data")
    if not isinstance(data, dict) or not data.get("상품"):
        raise HTTPException(status_code=400, detail="상품 목록이 비어 있습니다")
    DISK_DIR.mkdir(parents=True, exist_ok=True)
    (DISK_DIR / f"{slug}.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return JSONResponse({"ok": True, "slug": slug, "상품수": len(data["상품"])})
