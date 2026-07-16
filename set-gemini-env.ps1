# 1) Gemini の API キーをコピーする
# 2) このファイルを実行する
# 3) 聞かれたら Ctrl+V で貼り付けて Enter

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host ""
Write-Host "ここに Gemini API キーを貼り付けてください (Ctrl+V) → Enter" -ForegroundColor Cyan
$key = Read-Host "Paste key here"

$key = $key.Trim()
if ($key.Length -lt 20) {
  Write-Host "短すぎます。AI Studio の Copy でもう一度コピーしてやり直してください。" -ForegroundColor Red
  exit 1
}

Write-Host "Vercel に保存しています..." -ForegroundColor Yellow
$key | vercel env add GEMINI_API_KEY production --force
if ($LASTEXITCODE -ne 0) { throw "production failed" }
$key | vercel env add GEMINI_API_KEY preview --force
if ($LASTEXITCODE -ne 0) { throw "preview failed" }

$key = $null
Write-Host "再公開しています..." -ForegroundColor Yellow
vercel --yes --prod
if ($LASTEXITCODE -ne 0) { throw "deploy failed" }

Write-Host ""
Write-Host "完了！ https://sns-risk-checker.vercel.app を開いてください" -ForegroundColor Green
