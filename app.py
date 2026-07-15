import os
import tempfile
from pathlib import Path

from flask import Flask, render_template, request
from google import genai
from google.genai import types
from supabase import create_client

BASE_DIR = Path(__file__).resolve().parent

app = Flask(__name__, template_folder=str(BASE_DIR / "templates"))
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024  # Vercel向けに 8MB まで

# 以前動いた無料枠向けモデルを優先
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
MODEL_CANDIDATES = (
    GEMINI_MODEL,
    "gemini-2.5-flash",
    "gemini-flash-latest",
    "gemini-2.0-flash",
)

SYSTEM_PROMPT = """あなたはSNS（X / TikTok / YouTube）の炎上リスクを判定する広報コンサルタントです。
文章・画像・動画（音声・字幕・画面上の文字・演出・テロップ）を総合して分析し、必ず次の形式だけを日本語で回答してください。

# 炎上リスクスコア: [0-100点]
### 判定対象の要約
（何を投稿しようとしているか1〜2文）
### 判定理由
（簡潔に）
### 特に注意すべき箇所
（文章／映像／音声／字幕／画像それぞれ該当するものを箇条書き）
### 改善案
（炎上を避ける言い換え・撮り直し・編集・キャプション案を3つ）
"""


def get_api_key() -> str:
    return (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or "").strip()


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
            last_error = RuntimeError(
                f"Geminiへの接続に失敗しました（モデル: {model_name}）。"
                f"詳細: {type(exc).__name__}: {exc}"
            )
            message = str(exc).lower()
            if any(
                token in message
                for token in (
                    "429",
                    "resource_exhausted",
                    "quota",
                    "404",
                    "not_found",
                    "no longer available",
                )
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
        # Gemini は公開 YouTube URL を直接解析できる
        parts.append(
            types.Part(
                file_data=types.FileData(file_uri=youtube_url)
            )
        )

    if image_file and image_file.filename:
        image_bytes = image_file.read()
        if not image_bytes:
            raise RuntimeError("画像ファイルが空です。")
        mime = image_file.mimetype or "image/jpeg"
        if not mime.startswith("image/"):
            raise RuntimeError("画像ファイル（JPG / PNG / WEBP など）を選んでください。")
        parts.append(
            types.Part(
                inline_data=types.Blob(data=image_bytes, mime_type=mime)
            )
        )

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

            # 小さめならインライン、大きめなら Files API
            if len(video_bytes) <= 15 * 1024 * 1024:
                parts.append(
                    types.Part(
                        inline_data=types.Blob(data=video_bytes, mime_type=mime)
                    )
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
            # TikTok URL は直接未対応なことが多いので、URLと文面ベースで注意喚起付き判定
            parts.append(
                types.Part(
                    text=(
                        "※TikTok URL は動画本体を直接取得できない場合があります。"
                        "URL・キャプション・投稿意図から炎上リスクを推定し、"
                        "映像確認ができていない旨も改善案に含めてください。"
                        "より正確な判定には動画ファイルのアップロードを勧めてください。"
                    )
                )
            )

        if len(parts) == 1:
            raise RuntimeError(
                "判定する内容がありません。"
                "文章、画像、YouTube URL、TikTok URL、動画ファイルのいずれかを入力してください。"
            )

        return generate_with_fallback(
            types.Content(role="user", parts=parts)
        )
    finally:
        if client and uploaded is not None:
            try:
                client.files.delete(name=uploaded.name)
            except Exception:
                pass


@app.route("/health")
def health():
    api_key = get_api_key()
    return {
        "ok": True,
        "has_gemini_key": bool(api_key),
        "gemini_key_length": len(api_key),
        "model": GEMINI_MODEL,
        "supports": ["text", "image", "youtube", "tiktok_url", "video_upload"],
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

    if request.method == "POST":
        caption = (request.form.get("caption") or request.form.get("input_text") or "").strip()
        youtube_url = (request.form.get("youtube_url") or "").strip()
        tiktok_url = (request.form.get("tiktok_url") or "").strip()
        image_file = request.files.get("image_file")
        video_file = request.files.get("video_file")

        has_image = bool(image_file and image_file.filename)
        has_video = bool(video_file and video_file.filename)

        if not any([caption, youtube_url, tiktok_url, has_image, has_video]):
            error = "文章・画像・YouTube・TikTok・動画のいずれかを入力してください。"
        else:
            try:
                result = analyze_content(
                    caption=caption,
                    youtube_url=youtube_url,
                    tiktok_url=tiktok_url,
                    image_file=image_file,
                    video_file=video_file,
                )
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
                    error = "判定は完了しましたが、Supabaseへの保存に失敗しました。"
            except RuntimeError as exc:
                error = str(exc)
            except Exception as exc:
                error = f"AI判定に失敗しました。（{type(exc).__name__}: {exc}）"

    return render_template(
        "index.html",
        result=result,
        error=error,
        caption=caption,
        youtube_url=youtube_url,
        tiktok_url=tiktok_url,
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
