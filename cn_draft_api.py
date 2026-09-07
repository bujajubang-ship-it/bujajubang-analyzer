"""Authenticated Render proxy for persistent CN draft jobs."""
import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response


def create_router(auth, base, secret, apply_logo):
    router = APIRouter()

    async def relay(request: Request, suffix: str = ''):
        if not auth(request):
            return JSONResponse({'error': '로그인이 필요합니다.'}, status_code=401)
        if suffix not in ('', 'action', 'image', 'download'):
            return JSONResponse({'error': '잘못된 요청입니다.'}, status_code=404)
        path = '/cnmaker/drafts' + ('/' + suffix if suffix else '')
        try:
            data = None
            if request.method == 'POST':
                raw = await request.body()
                if len(raw) > 30 * 1024 * 1024:
                    return JSONResponse({'error': '사진 용량이 너무 큽니다.'}, status_code=413)
                import json
                data = json.loads(raw)
            async with httpx.AsyncClient(timeout=60) as client:
                response = await client.request(request.method, base + path, params=request.query_params,
                                                json=data, headers={'x-secret': secret})
            if response.status_code != 200:
                try:
                    error = response.json()
                except ValueError:
                    error = {'error': '작업 서버 응답을 확인하지 못했습니다. 다시 시도해 주세요.'}
                return JSONResponse(error, status_code=response.status_code)
            if suffix in ('image', 'download'):
                content = response.content
                mime = response.headers.get('content-type', 'image/jpeg').split(';')[0]
                headers = {'Cache-Control': 'no-store'}
                if suffix == 'download':
                    if mime == 'image/jpeg':
                        content = apply_logo(content)
                    extension = 'zip' if mime == 'application/zip' else 'jpg'
                    headers['Content-Disposition'] = f'attachment; filename="cnmaker-{request.query_params.get("quality", "low")}.{extension}"'
                return Response(content, media_type=mime, headers=headers)
            return JSONResponse(response.json(), headers={'Cache-Control': 'no-store'})
        except ValueError:
            return JSONResponse({'error': '요청 형식을 확인해 주세요.'}, status_code=400)
        except httpx.HTTPError:
            return JSONResponse({'error': '작업 서버 연결이 지연됩니다. 잠시 뒤 다시 확인해 주세요.'}, status_code=502)

    router.add_api_route('/cnmaker/api/drafts', relay, methods=['GET', 'POST'])
    router.add_api_route('/cnmaker/api/drafts/{suffix}', relay, methods=['GET', 'POST'])
    return router
