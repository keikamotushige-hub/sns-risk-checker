import os
import secrets
import tempfile
import threading
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path

from flask import (
    Flask,
    flash,
    make_response,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from google import genai
from google.genai import types
from supabase import create_client

BASE_DIR = Path(__file__).resolve().parent

app = Flask(__name__, template_folder=str(BASE_DIR / "templates"))
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(32)

GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
MODEL_CANDIDATES = (
    GEMINI_MODEL,
    "gemini-2.5-flash",
    "gemini-flash-latest",
    "gemini-2.0-flash",
)

DAILY_GLOBAL_LIMIT = int(os.environ.get("DAILY_GLOBAL_LIMIT", "40"))
PER_PERSON_LIMIT = int(os.environ.get("PER_PERSON_LIMIT", "3"))
USAGE_COOKIE = "sns_risk_uses"

_usage_lock = threading.Lock()
_usage_day = ""
_usage_global = 0
_usage_by_key: dict[str, int] = {}

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


def get_supabase():
    url = (os.environ.get("SUPABASE_URL") or "").strip()
    key = (os.environ.get("SUPABASE_KEY") or "").strip()
    if not url or not key:
        return None
    return create_client(url, key)


def auth_mode() -> str:
    """supabase | passcode | unset"""
    if get_supabase() is not None:
        return "supabase"
    if (os.environ.get("SITE_PASSCODE") or "").strip():
        return "passcode"
    return "unset"


def current_user() -> dict | None:
    user = session.get("user")
    if isinstance(user, dict) and user.get("id") and user.get("email"):
        return user
    return None


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user():
            flash("判定を使うにはログインしてください。", "error")
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)

    return wrapped


def _today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _person_key(ip: str) -> str:
    user = current_user()
    if user:
        return f"user:{user['id']}"
    return f"ip:{ip or 'unknown'}"


def _read_cookie_uses() -> tuple[str, int]:
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


def _db_user_uses(user_id: str) -> int | None:
    client = get_supabase()
    if client is None:
        return None
    today = _today_utc()
    try:
        res = (
            client.table("user_daily_usage")
            .select("use_count")
            .eq("user_id", user_id)
            .eq("usage_date", today)
            .limit(1)
            .execute()
        )
        rows = res.data or []
        if not rows:
            return 0
        return int(rows[0].get("use_count") or 0)
    except Exception:
        return None


def _db_increment_user_uses(user_id: str) -> int | None:
    client = get_supabase()
    if client is None:
        return None
    today = _today_utc()
    current = _db_user_uses(user_id)
    if current is None:
        return None
    next_count = current + 1
    try:
        client.table("user_daily_usage").upsert(
            {
                "user_id": user_id,
                "usage_date": today,
                "use_count": next_count,
            },
            on_conflict="user_id,usage_date",
        ).execute()
        return next_count
    except Exception:
        return None


def _person_used(ip: str) -> int:
    user = current_user()
    if user:
        db_uses = _db_user_uses(user["id"])
        if db_uses is not None:
            return db_uses

    _, cookie_uses = _read_cookie_uses()
    key = _person_key(ip)
    with _usage_lock:
        today = _today_utc()
        global _usage_day, _usage_by_key
        if _usage_day != today:
            return cookie_uses if not user else 0
        return max(cookie_uses if not user else 0, _usage_by_key.get(key, 0))


def check_quota_locked(ip: str) -> str | None:
    global _usage_day, _usage_global, _usage_by_key
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
            _usage_by_key = {}
        if _usage_global >= DAILY_GLOBAL_LIMIT:
            return (
                f"本日の無料判定上限（全体 {DAILY_GLOBAL_LIMIT} 回）に達したため停止しました。"
                "明日になってから再度お試しください。"
            )
    return None


def consume_quota(ip: str) -> int:
    global _usage_day, _usage_global, _usage_by_key
    user = current_user()
    if user:
        db_count = _db_increment_user_uses(user["id"])
        if db_count is not None:
            with _usage_lock:
                today = _today_utc()
                if _usage_day != today:
                    _usage_day = today
                    _usage_global = 0
                    _usage_by_key = {}
                _usage_global += 1
            return db_count

    key = _person_key(ip)
    today, cookie_uses = _read_cookie_uses()
    with _usage_lock:
        if _usage_day != today:
            _usage_day = today
            _usage_global = 0
            _usage_by_key = {}
        _usage_global += 1
        mem_uses = _usage_by_key.get(key, 0) + 1
        _usage_by_key[key] = mem_uses
        if user:
            return mem_uses
        return max(cookie_uses + 1, mem_uses)


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


def save_to_supabase(input_text: str, result: str, user_id: str | None = None) -> None:
    client = get_supabase()
    if client is None:
        return

    row = {
        "input_text": input_text,
        "result": result,
    }
    if user_id:
        row["user_id"] = user_id

    client.table("risk_checks").insert(row).execute()


@app.route("/health")
def health():
    api_key = get_api_key()
    ip = request.headers.get("X-Forwarded-For", request.remote_addr or "unknown").split(",")[0].strip()
    return {
        "ok": True,
        "auth_mode": auth_mode(),
        "logged_in": bool(current_user()),
        "has_gemini_key": bool(api_key),
        "gemini_key_length": len(api_key),
        "model": GEMINI_MODEL,
        "quota": remaining_quota(ip),
    }


@app.route("/signup", methods=["GET", "POST"])
def signup():
    mode = auth_mode()
    if mode == "passcode":
        flash("現在は共通パスコード方式です。新規登録は不要です。ログインへ進んでください。", "error")
        return redirect(url_for("login"))
    if mode == "unset":
        return render_template(
            "login.html",
            mode=mode,
            error="ログイン設定が未完了です。Vercel に SUPABASE_URL / SUPABASE_KEY、または SITE_PASSCODE を設定してください。",
            email="",
        )

    error = None
    email = ""
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        password2 = request.form.get("password2") or ""

        if not email or not password:
            error = "メールアドレスとパスワードを入力してください。"
        elif len(password) < 6:
            error = "パスワードは6文字以上にしてください。"
        elif password != password2:
            error = "確認用パスワードが一致しません。"
        else:
            client = get_supabase()
            try:
                res = client.auth.sign_up({"email": email, "password": password})
                user = getattr(res, "user", None)
                session_obj = getattr(res, "session", None)
                if user and session_obj:
                    session["user"] = {
                        "id": user.id,
                        "email": user.email or email,
                    }
                    flash("登録してログインしました。", "ok")
                    return redirect(url_for("index"))
                flash(
                    "登録を受け付けました。メール確認が必要な設定の場合は、届いたメールを確認してからログインしてください。",
                    "ok",
                )
                return redirect(url_for("login"))
            except Exception as exc:
                error = f"登録に失敗しました。（{exc}）"

    return render_template("signup.html", mode=mode, error=error, email=email)


@app.route("/login", methods=["GET", "POST"])
def login():
    mode = auth_mode()
    error = None
    email = ""

    if mode == "unset":
        return render_template(
            "login.html",
            mode=mode,
            error="ログイン設定が未完了です。Vercel に SUPABASE_URL / SUPABASE_KEY、または SITE_PASSCODE を設定してください。",
            email="",
        )

    if request.method == "POST":
        if mode == "passcode":
            passcode = request.form.get("passcode") or ""
            expected = (os.environ.get("SITE_PASSCODE") or "").strip()
            if passcode and passcode == expected:
                session["user"] = {"id": "passcode-user", "email": "passcode@local"}
                flash("ログインしました。", "ok")
                next_url = request.args.get("next") or url_for("index")
                return redirect(next_url)
            error = "パスコードが正しくありません。"
        else:
            email = (request.form.get("email") or "").strip().lower()
            password = request.form.get("password") or ""
            if not email or not password:
                error = "メールアドレスとパスワードを入力してください。"
            else:
                client = get_supabase()
                try:
                    res = client.auth.sign_in_with_password(
                        {"email": email, "password": password}
                    )
                    user = getattr(res, "user", None)
                    if not user:
                        error = "ログインに失敗しました。"
                    else:
                        session["user"] = {
                            "id": user.id,
                            "email": user.email or email,
                        }
                        flash("ログインしました。", "ok")
                        next_url = request.args.get("next") or url_for("index")
                        return redirect(next_url)
                except Exception as exc:
                    error = f"ログインに失敗しました。（{exc}）"

    return render_template("login.html", mode=mode, error=error, email=email)


@app.route("/logout")
def logout():
    session.clear()
    flash("ログアウトしました。", "ok")
    return redirect(url_for("login"))


@app.route("/", methods=["GET", "POST"])
@login_required
def index():
    result = None
    error = None
    caption = ""
    youtube_url = ""
    tiktok_url = ""
    agreed = False
    new_cookie_count = None
    user = current_user()
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
                        save_to_supabase(
                            summary or "(media)",
                            result,
                            user_id=user["id"] if user else None,
                        )
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
        user=user,
        auth_mode=auth_mode(),
    )
    response = make_response(html)
    if new_cookie_count is not None and not user:
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
