import os
from pathlib import Path

from flask import Flask, render_template, request
from openai import OpenAI, OpenAIError
from supabase import create_client

BASE_DIR = Path(__file__).resolve().parent

app = Flask(__name__, template_folder=str(BASE_DIR / "templates"))

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
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "環境変数 OPENAI_API_KEY が設定されていません。"
            "Vercel またはローカルの環境変数に API キーを設定してください。"
        )

    client = OpenAI(api_key=api_key)
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ],
        temperature=0.3,
    )
    content = response.choices[0].message.content
    if not content:
        raise RuntimeError("AIから有効な応答を取得できませんでした。")
    return content


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
                    # 判定結果は返す。保存失敗はユーザー向けに簡潔に通知。
                    error = "判定は完了しましたが、Supabaseへの保存に失敗しました。"
            except OpenAIError:
                error = "AI判定に失敗しました。しばらくしてから再度お試しください。"
            except RuntimeError as exc:
                error = str(exc)
            except Exception:
                error = "予期しないエラーが発生しました。しばらくしてから再度お試しください。"

    return render_template(
        "index.html",
        result=result,
        error=error,
        input_text=input_text,
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
