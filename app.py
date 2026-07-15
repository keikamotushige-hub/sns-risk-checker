import os
from pathlib import Path

from flask import Flask, render_template, request
from google import genai
from supabase import create_client

BASE_DIR = Path(__file__).resolve().parent

app = Flask(__name__, template_folder=str(BASE_DIR / "templates"))

# 無料枠向けの軽量モデル（枠が 0 のときは GEMINI_MODEL を別モデルに変更）
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash-lite")

SYSTEM_PROMPT = """あなたはSNS炎上リスクを判定するコンサルタントです。ユーザーの投稿文を分析し、以下の形式で回答してください。
# 炎上リスクスコア: [0-100点]
### 判定理由
（簡潔に）
### 特に注意すべき箇所
（箇条書き）
### 改善案
（炎上を避けるための言い換え案を3つ）
"""


def analyze_text(text: str) -> str:
    api_key = (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError(
            "サーバーに APIキー（GEMINI_API_KEY）が入っていません。"
            "Vercel の Environment Variables に設定してください。"
            f"（現在のキー文字数: 0 / モデル: {GEMINI_MODEL}）"
        )

    prompt = f"{SYSTEM_PROMPT}\n\n【投稿文】\n{text}"
    models_to_try = []
    for name in (GEMINI_MODEL, "gemini-2.5-flash-lite", "gemini-2.5-flash", "gemini-2.0-flash"):
        if name not in models_to_try:
            models_to_try.append(name)

    client = genai.Client(api_key=api_key)
    last_error = None
    for model_name in models_to_try:
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
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
            message = str(exc)
            if "429" in message or "RESOURCE_EXHAUSTED" in message or "quota" in message.lower():
                continue
            raise last_error from exc

    raise last_error or RuntimeError("AI判定に失敗しました。")


@app.route("/health")
def health():
    api_key = (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or "").strip()
    return {
        "ok": True,
        "has_gemini_key": bool(api_key),
        "gemini_key_length": len(api_key),
        "model": GEMINI_MODEL,
    }


def save_to_supabase(input_text: str, result: str) -> None:
    """判定結果を Supabase に保存する。未設定ならスキップ。"""
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
    input_text = ""

    if request.method == "POST":
        input_text = (request.form.get("input_text") or "").strip()

        if not input_text:
            error = "投稿テキストを入力してください。"
        else:
            try:
                result = analyze_text(input_text)
                try:
                    save_to_supabase(input_text, result)
                except Exception:
                    error = "判定は完了しましたが、Supabaseへの保存に失敗しました。"
            except RuntimeError as exc:
                error = str(exc)
            except Exception as exc:
                error = (
                    "AI判定に失敗しました。"
                    f"（{type(exc).__name__}: {exc}）"
                )

    return render_template(
        "index.html",
        result=result,
        error=error,
        input_text=input_text,
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
