"""Shared GPT transport for product vision, translation and copywriting."""
import json
import os
from pathlib import Path
import urllib.error
import urllib.request


def _setting(name, default=""):
    value = os.getenv(name)
    if value:
        return value.strip()
    path = Path(__file__).with_name("cn.env")
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            key, sep, value = line.partition("=")
            if sep and key.strip() == name:
                return value.strip()
    return default


MODEL = _setting("CN_ANALYSIS_MODEL", "gpt-6-astra")
REASONING = _setting("CN_ANALYSIS_REASONING", "low")
CACHE_ID = "openai-responses-v1:" + MODEL + ":" + REASONING


def payload(content, max_tokens):
    if isinstance(content, str):
        content = [{"type": "text", "text": content}]
    parts = []
    for item in content:
        if item["type"] == "text":
            parts.append({"type": "input_text", "text": item["text"]})
        elif item["type"] == "image":
            source = item["source"]
            if source["type"] == "base64":
                url = "data:" + source["media_type"] + ";base64," + source["data"]
            elif source["type"] == "url":
                url = source["url"]
            else:
                raise ValueError("Unsupported image source")
            parts.append({"type": "input_image", "image_url": url, "detail": "high"})
        else:
            raise ValueError("Unsupported analysis content")
    return {"model": MODEL, "store": False,
            "reasoning": {"effort": REASONING},
            # Responses budgets include reasoning, unlike legacy visible-text limits.
            "max_output_tokens": max(4096, int(max_tokens) + 4096),
            "input": [{"role": "user", "content": parts}]}


def output_text(data):
    if data.get("status") != "completed":
        raise RuntimeError("GPT 분석 응답이 완료되지 않았습니다.")
    result = []
    for item in data.get("output", []):
        if item.get("type") != "message":
            continue
        for part in item.get("content", []):
            if part.get("type") == "refusal":
                raise RuntimeError("GPT가 분석 요청에 응답하지 못했습니다.")
            if part.get("type") == "output_text":
                result.append(part.get("text", ""))
    text = "".join(result).strip()
    if not text:
        raise RuntimeError("GPT 분석 결과가 비어 있습니다.")
    return text


def complete(content, max_tokens=2000):
    key = _setting("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("OPENAI_API_KEY 미설정")
    request = urllib.request.Request(
        "https://api.openai.com/v1/responses",
        data=json.dumps(payload(content, max_tokens), ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            data = json.load(response)
    except urllib.error.HTTPError as error:
        # Never expose request headers, image payloads or provider error bodies.
        raise RuntimeError(f"GPT 분석 요청 실패 (HTTP {error.code}, 모델 {MODEL})") from None
    return output_text(data)
