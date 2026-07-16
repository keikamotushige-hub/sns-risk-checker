# SNS Risk Checker

Gemini による SNS 投稿前の炎上リスク参考チェック。ログイン必須。

## 権限

| 役割 | メール | 回数 |
|------|--------|------|
| オーナー | `keikamotushige@gmail.com` | **無制限** |
| 一般ユーザー | 任意の登録アカウント | **無料トライアル 3 回**（通算） |

環境変数 `OWNER_EMAILS` で追加オーナーをカンマ区切り指定できます。

## データベース（GitHub に保存）

| ファイル | 用途 |
|----------|------|
| `supabase_schema.sql` | 本番 Supabase（判定履歴 + トライアル回数） |
| `db/local_schema.sql` | VirtualBox / ローカル SQLite |
| `db/collection_storyteller_schema.sql` | Collection Storyteller 用（将来） |

ローカル実行時は `data/app.db` に自動作成されます（`.gitignore` 対象）。

## ログイン

1. **local**（デフォルト / VirtualBox）: SQLite にメール・パスワード登録
2. **supabase**: `SUPABASE_URL` + `SUPABASE_KEY`
3. **passcode**: `SITE_PASSCODE` のみ

## ローカル起動

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
set GEMINI_API_KEY=your_key   # PowerShell: $env:GEMINI_API_KEY="..."
python app.py
```

http://127.0.0.1:5000 を開き、新規登録 → ログイン。

## VirtualBox デプロイ（Vagrant）

前提: [VirtualBox](https://www.virtualbox.org/) と [Vagrant](https://www.vagrantup.com/) をインストール。

```bash
# Windows PowerShell（リポジトリ直下）
$env:GEMINI_API_KEY = "your_gemini_api_key"
vagrant up
```

- アプリ: http://192.168.56.10:5000 または http://localhost:5000
- オーナーでログイン: `keikamotushige@gmail.com`（新規登録して同じメールを使う）
- 一般ユーザーは 3 回まで

停止 / 再起動:

```bash
vagrant halt
vagrant up
vagrant ssh
sudo systemctl status sns-risk-checker
```

## Supabase（クラウド）

1. `supabase_schema.sql` を SQL Editor で実行
2. 環境変数: `SUPABASE_URL`, `SUPABASE_KEY`, `GEMINI_API_KEY`, `OWNER_EMAILS=keikamotushige@gmail.com`

## 環境変数

| 変数 | 説明 | デフォルト |
|------|------|------------|
| `GEMINI_API_KEY` | Gemini API キー | （必須） |
| `OWNER_EMAILS` | 無制限オーナー | `keikamotushige@gmail.com` |
| `PER_PERSON_LIMIT` | 無料トライアル回数 | `3` |
| `AUTH_MODE` | `local` 強制 | 未設定時は自動判定 |
| `DATA_DIR` | SQLite 保存先 | `./data` |
| `SECRET_KEY` | Flask セッション鍵 | 自動生成 |
