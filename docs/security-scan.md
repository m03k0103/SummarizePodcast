# SummarizePodcast セキュリティスキャン (OWASP ZAP DAST)

本プロジェクトでは、GitHub Actions (`.github/workflows/zap-dast.yml`) により OWASP ZAP (`ghcr.io/zaproxy/zaproxy:stable`) を用いた動的脆弱性スキャン（DAST）を自動実行しています。

## 1. CI/CD パイプライン仕様

| 項目 | 設定内容 |
| :--- | :--- |
| **PR / `main` プッシュ時** | `zap-baseline.py`（パッシブスキャン中心・高速チェック） |
| **定期実行（毎週日曜 04:00 JST）/ 手動実行 (`full`)** | `zap-full-scan.py`（アクティブスキャンを含む詳細チェック） |
| **CI 対象サーバー** | `http://127.0.0.1:8005/`（CI 環境内で一時起動するローカルサーバーのみ） |
| **失敗基準** | **High** (`riskcode == 3`) 検出時のみ CI 失敗 (`exit 1`)。Medium / Low は警告出力のみ |
| **レポート保管** | Artifact `zap-scan-report`（HTML / JSON / Markdown）＋ Job Summary ＋ PR コメント |

> **安全制約**: `.zap/evaluate_zap_report.py --validate-target` により、`127.0.0.1` / `localhost` / `host.docker.internal` 以外の外部 URL や本番 URL へのスキャンは実行前に拒否されます。

## 2. ローカル環境での再現手順

Docker Desktop / Docker Engine がインストールされている環境で、ローカルサーバーを起動してからスキャンを実行してください。

### ステップ 1: ローカルサーバー起動（別ターミナル）

```bash
python -m streamlit run app.py --server.port 8005 --server.address 127.0.0.1 --server.headless true
```

### ステップ 2: ZAP ベースラインスキャン実行

**Bash / Git Bash / WSL の場合:**
```bash
./scripts/run-zap-scan.sh baseline
```

**Windows PowerShell の場合:**
```powershell
.\scripts\run-zap-scan.ps1 -ScanMode baseline
```

**Docker ワンライナーで直接実行する場合:**
```bash
mkdir -p .zap/reports && cp .zap/rules.tsv .zap/reports/rules.tsv
docker run --rm --add-host=host.docker.internal:host-gateway \
  -v "$(pwd)/.zap/reports:/zap/wrk/:rw" \
  ghcr.io/zaproxy/zaproxy:stable \
  zap-baseline.py -t http://host.docker.internal:8005/ \
  -c rules.tsv -J report_json.json -w report_md.md -r report_html.html -I
```

スキャン完了後、`.zap/reports/report_html.html` および `.zap/reports/summary.md` に結果が出力されます。

## 3. ルールチューニング方法 (`.zap/rules.tsv`)

誤検知（False Positive）やテスト環境特有の無害なヘッダー警告を調整する場合は、`.zap/rules.tsv` を編集します。

- 形式は **必ず TAB (`\t`) 区切り**（`<plugin_id>\t<IGNORE|WARN|FAIL|OUTOFSCOPE>\t(<説明またはURL正規表現>)`）を使用してください。
- 例: 特定ルールを無効化する場合:
  ```tsv
  10038	IGNORE	(Content Security Policy Header Not Set - managed by CDN)
  ```
- 詳細な共通仕様は `D:\dev\Orchestration\docs\security-scan.md` を参照してください。
