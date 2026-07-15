import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, make_response, render_template, request
from google import genai
from google.genai import types
from supabase import create_client

BASE_DIR = Path(__file__).resolve().parent

app = Flask(__name__, template_folder=str(BASE_DIR / "templates"))
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024

# 以前動作確認できた無料枠向けモデル
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
MODEL_CANDIDATES = (
    GEMINI_MODEL,
    "gemini-2.5-flash",
    "gemini-flash-latest",
    "gemini-2.0-flash",
)

# 公開サイトの無料枠保護（1人3回まで。超過でロック）
DAILY_GLOBAL_LIMIT = int(os.environ.get("DAILY_GLOBAL_LIMIT", "40"))
PER_PERSON_LIMIT = int(os.environ.get("PER_PERSON_LIMIT", "3"))
USAGE_COOKIE = "sns_risk_uses"

_usage_lock = threading.Lock()
_usage_day = ""
_usage_global = 0
_usage_by_ip: dict[str, int] = {}

SYSTEM_PROMPT = """あなたはSNS（X / TikTok / YouTube）投稿前の参考チェックをするアシスタントです。
これは法的助言・検閲・投稿可否の最終判断ではありません。あくまでリスクの参考情報です。
文章・画像・動画を総合して分析し、必ず次の形式だけを日本語で回答してください。

# 炎上リスクスコア: [0-100点]
### 判定対象の要約
（何を投稿しようとしているか1〜2文）
### 判定理由
（簡潔に）
### 特に注意すべき箇所
（文章／映像／音声／字幕／画像それぞれ該当するものを箇条書き）
### 改善案
（炎上リスクを下げる言い換え・編集・キャプション案を3つ）
### 注意
- 本結果は参考情報であり、正確性・完全性を保証しないこと
- 最終的な投稿判断は利用者本人が行うこと
"""

QUOTA_STOP_MESSAGE = (
    "無料枠を超えたため、判定を自動停止しました。"
    "Google側の請求設定を付けていなければ、通常は追加課金されません。"
    "枠が回復するまでお待ちください。"
)


def get_api_key() -> str:
    return (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or "").strip()


def _today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _read_cookie_uses() -> tuple[str, int]:
    """cookie: 'YYYY-MM-DD:count'。日付が違えばリセット。"""
    raw = (request.cookies.get(USAGE_COOKIE) or "").strip()
    today = _today_utc()
    if not raw or ":" not in raw:
        return today, 0
    day, count_str = raw.split(":", 1)
    if day != today:
        return today, 0
    try:
        return today, max(0, int(count_str))
    except ValueError:
        return today, 0


def _person_used(ip: str) -> int:
    """IP と Cookie の多い方を本人利用回数とみなす。"""
    _, cookie_uses = _read_cookie_uses()
    with _usage_lock:
        today = _today_utc()
        global _usage_day, _usage_by_ip
        if _usage_day != today:
            return cookie_uses
        return max(cookie_uses, _usage_by_ip.get(ip or "unknown", 0))


def check_quota_locked(ip: str) -> str | None:
    """ロック中ならエラー文言。まだ使えるなら None。"""
    global _usage_day, _usage_global, _usage_by_ip
    ip = ip or "unknown"
    used = _person_used(ip)
    if used >= PER_PERSON_LIMIT:
        return (
            f"お一人様あたり本日 {PER_PERSON_LIMIT} 回までの無料チェックです。"
            "上限に達したためロックしました。明日（日付が変わったら）再度お試しください。"
        )

    with _usage_lock:
        today = _today_utc()
        if _usage_day != today:
            _usage_day = today
            _usage_global = 0
            _usage_by_ip = {}
        if _usage_global >= DAILY_GLOBAL_LIMIT:
            return (
                f"本日の無料判定上限（全体 {DAILY_GLOBAL_LIMIT} 回）に達したため停止しました。"
                "明日になってから再度お試しください。"
            )
    return None


def consume_quota(ip: str) -> int:
    """1回消費し、消費後の本人回数を返す。"""
    global _usage_day, _usage_global, _usage_by_ip
    ip = ip or "unknown"
    today, cookie_uses = _read_cookie_uses()
    with _usage_lock:
        if _usage_day != today:
            _usage_day = today
            _usage_global = 0
            _usage_by_ip = {}
        _usage_global += 1
        ip_uses = _usage_by_ip.get(ip, 0) + 1
        _usage_by_ip[ip] = ip_uses
        return max(cookie_uses + 1, ip_uses)


def remaining_quota(ip: str) -> dict:
    used = _person_used(ip)
    with _usage_lock:
        today = _today_utc()
        global_remaining = DAILY_GLOBAL_LIMIT
        if _usage_day == today:
            global_remaining = max(0, DAILY_GLOBAL_LIMIT - _usage_global)
        return {
            "day": today,
            "global_remaining": global_remaining,
            "person_remaining": max(0, PER_PERSON_LIMIT - used),
            "person_used": used,
            "person_limit": PER_PERSON_LIMIT,
            "locked": used >= PER_PERSON_LIMIT,
        }


def generate_with_fallback(contents) -> str:
    api_key = get_api_key()
    if not api_key:
        raise RuntimeError(
            "サーバーに APIキー（GEMINI_API_KEY）が入っていません。"
            "Vercel の Environment Variables に設定してください。"
        )

    client = genai.Client(api_key=api_key)
    last_error = None
    models_to_try = []
    for name in MODEL_CANDIDATES:
        if name not in models_to_try:
            models_to_try.append(name)

    for model_name in models_to_try:
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=contents,
            )
            content = (getattr(response, "text", None) or "").strip()
            if content:
                return content
            last_error = RuntimeError("AIから有効な応答を取得できませんでした。")
        except Exception as exc:
            message = str(exc).lower()
            if any(
                token in message
                for token in ("429", "resource_exhausted", "quota", "rate limit")
            ):
                raise RuntimeError(QUOTA_STOP_MESSAGE) from exc

            last_error = RuntimeError(
                f"Geminiへの接続に失敗しました（モデル: {model_name}）。"
                f"詳細: {type(exc).__name__}: {exc}"
            )
            if any(
                token in message
                for token in ("404", "not_found", "no longer available")
            ):
                continue
            raise last_error from exc

    raise last_error or RuntimeError("AI判定に失敗しました。")


def analyze_content(
    caption: str = "",
    youtube_url: str = "",
    tiktok_url: str = "",
    image_file=None,
    video_file=None,
) -> str:
    parts = [types.Part(text=SYSTEM_PROMPT)]

    meta_lines = []
    if caption:
        meta_lines.append(f"【投稿文 / キャプション】\n{caption}")
    if youtube_url:
        meta_lines.append(f"【YouTube URL】\n{youtube_url}")
    if tiktok_url:
        meta_lines.append(f"【TikTok URL】\n{tiktok_url}")

    if meta_lines:
        parts.append(types.Part(text="\n\n".join(meta_lines)))

    if youtube_url:
        parts.append(types.Part(file_data=types.FileData(file_uri=youtube_url)))

    if image_file and image_file.filename:
        image_bytes = image_file.read()
        if not image_bytes:
            raise RuntimeError("画像ファイルが空です。")
        mime = image_file.mimetype or "image/jpeg"
        if not mime.startswith("image/"):
            raise RuntimeError("画像ファイル（JPG / PNG / WEBP など）を選んでください。")
        parts.append(types.Part(inline_data=types.Blob(data=image_bytes, mime_type=mime)))

    uploaded = None
    client = None
    try:
        if video_file and video_file.filename:
            video_bytes = video_file.read()
            if not video_bytes:
                raise RuntimeError("動画ファイルが空です。")
            mime = video_file.mimetype or "video/mp4"
            if not mime.startswith("video/"):
                raise RuntimeError("動画ファイル（MP4 など）を選んでください。")

            if len(video_bytes) <= 15 * 1024 * 1024:
                parts.append(
                    types.Part(inline_data=types.Blob(data=video_bytes, mime_type=mime))
                )
            else:
                api_key = get_api_key()
                client = genai.Client(api_key=api_key)
                suffix = Path(video_file.filename).suffix or ".mp4"
                with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                    tmp.write(video_bytes)
                    tmp_path = tmp.name
                try:
                    uploaded = client.files.upload(file=tmp_path)
                    parts.append(
                        types.Part(
                            file_data=types.FileData(
                                file_uri=uploaded.uri,
                                mime_type=uploaded.mime_type or mime,
                            )
                        )
                    )
                finally:
                    Path(tmp_path).unlink(missing_ok=True)

        if tiktok_url and not (video_file and video_file.filename):
            parts.append(
                types.Part(
                    text=(
                        "※TikTok URL は動画本体を直接取得できない場合があります。"
                        "URL・キャプションから参考判定し、映像未確認であることも明記してください。"
                    )
                )
            )

        if len(parts) == 1:
            raise RuntimeError(
                "判定する内容がありません。"
                "文章、画像、YouTube URL、TikTok URL、動画ファイルのいずれかを入力してください。"
            )

        return generate_with_fallback(types.Content(role="user", parts=parts))
    finally:
        if client and uploaded is not None:
            try:
                client.files.delete(name=uploaded.name)
            except Exception:
                pass


@app.route("/health")
def health():
    api_key = get_api_key()
    ip = request.headers.get("X-Forwarded-For", request.remote_addr or "unknown").split(",")[0].strip()
    return {
        "ok": True,
        "has_gemini_key": bool(api_key),
        "gemini_key_length": len(api_key),
        "model": GEMINI_MODEL,
        "quota": remaining_quota(ip),
        "billing_note": "Keep Google Cloud billing disabled to stay free; quota errors stop requests.",
    }


def save_to_supabase(input_text: str, result: str) -> None:
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    if not url or not key:
        return

    client = create_client(url, key)
    client.table("risk_checks").insert(
        {
            "input_text": input_text,
            "result": result,
        }
    ).execute()


@app.route("/", methods=["GET", "POST"])
def index():
    result = None
    error = None
    caption = ""
    youtube_url = ""
    tiktok_url = ""
    agreed = False
    new_cookie_count = None
    ip = request.headers.get("X-Forwarded-For", request.remote_addr or "unknown").split(",")[0].strip()
    quota = remaining_quota(ip)

    if request.method == "POST":
        caption = (request.form.get("caption") or request.form.get("input_text") or "").strip()
        youtube_url = (request.form.get("youtube_url") or "").strip()
        tiktok_url = (request.form.get("tiktok_url") or "").strip()
        image_file = request.files.get("image_file")
        video_file = request.files.get("video_file")
        agreed = request.form.get("agree_terms") == "on"

        has_image = bool(image_file and image_file.filename)
        has_video = bool(video_file and video_file.filename)

        if not agreed:
            error = "利用前に「参考チェックであること」への同意が必要です。"
        elif not any([caption, youtube_url, tiktok_url, has_image, has_video]):
            error = "文章・画像・YouTube・TikTok・動画のいずれかを入力してください。"
        else:
            limit_error = check_quota_locked(ip)
            if limit_error:
                error = limit_error
            else:
                try:
                    result = analyze_content(
                        caption=caption,
                        youtube_url=youtube_url,
                        tiktok_url=tiktok_url,
                        image_file=image_file,
                        video_file=video_file,
                    )
                    new_cookie_count = consume_quota(ip)
                    summary = " / ".join(
                        x
                        for x in [
                            caption[:120] if caption else "",
                            youtube_url,
                            tiktok_url,
                            "image" if has_image else "",
                            "video" if has_video else "",
                        ]
                        if x
                    )
                    try:
                        save_to_supabase(summary or "(media)", result)
                    except Exception:
                        error = "判定は完了しましたが、保存に失敗しました。"
                except RuntimeError as exc:
                    error = str(exc)
                except Exception as exc:
                    message = str(exc).lower()
                    if any(t in message for t in ("429", "quota", "resource_exhausted")):
                        error = QUOTA_STOP_MESSAGE
                    else:
                        error = f"AI判定に失敗しました。（{type(exc).__name__}: {exc}）"
            quota = remaining_quota(ip)

    html = render_template(
        "index.html",
        result=result,
        error=error,
        caption=caption,
        youtube_url=youtube_url,
        tiktok_url=tiktok_url,
        agreed=agreed,
        quota=quota,
        daily_global_limit=DAILY_GLOBAL_LIMIT,
        per_person_limit=PER_PERSON_LIMIT,
    )
    response = make_response(html)
    if new_cookie_count is not None:
        response.set_cookie(
            USAGE_COOKIE,
            f"{_today_utc()}:{new_cookie_count}",
            max_age=60 * 60 * 36,
            httponly=True,
            samesite="Lax",
            secure=True,
        )
    return response


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
