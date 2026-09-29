# 測試說明

本文件說明以下兩支工具的使用方法與預期成果：

- `treesitter/generate_pact_tdd.py`
- `treesitter/generate_docker_mock.py`

兩者的定位不同：

- `generate_pact_tdd.py`：針對單一 Pact JSON 執行 RED/GREEN TDD 驗證，並產生 JSON 測試報告。
- `generate_docker_mock.py`：將所有 Karate feature 與 Pact contract 組合成 Pact stub server + Karate runner 的 Docker sandbox。

## 1. 測試前準備

請在專案根目錄 `TeDMMAv2` 執行下列命令：

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
.\.venv\Scripts\Activate.ps1
python -m pip install -r .\requirements.txt
```

如果尚未建立虛擬環境，可先執行：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

Mock Docker 測試另外需要：

- Docker Desktop
- Docker Compose
- 可連線的 Docker registry

測試輸入主要位於：

```text
treesitter/karate_feature/*.feature
treesitter/pact_contract/*.json
```

## 2. Pact TDD 驗證

### 2.1 使用目的

`generate_pact_tdd.py` 會讀取一份 Pact JSON，解析其中的 consumer、provider 與 interactions，依序執行：

1. RED light：確認尚未啟動的 Provider 會回傳 connection refused 或 HTTP 404。
2. GREEN light：使用 Pact Python 建立臨時 mock server，依 Pact contract 回應請求，並驗證 HTTP status 與 JSON response body。
3. 寫入完整測試報告。

### 2.2 使用預設契約

不指定 `--contract` 時，程式會依序搜尋：

1. `treesitter/pact_contract/spring-petclinic-main/*.json`
2. `treesitter/pact_contract/*.json`

若找到多份契約，會選擇最後修改的檔案。

```powershell
python .\treesitter\generate_pact_tdd.py
```

### 2.3 指定契約執行

例如驗證 `vet-service` 契約：

```powershell
python .\treesitter\generate_pact_tdd.py `
  --contract .\treesitter\pact_contract\vet-service_contract.json
```

也可以指定 RED 階段探測的 Provider URL 與報告位置：

```powershell
python .\treesitter\generate_pact_tdd.py `
  --contract .\treesitter\pact_contract\vet-service_contract.json `
  --provider-url http://localhost:8082 `
  --report .\scratch\vet-service_tdd_report.json
```

可用參數：

| 參數 | 預設值 | 說明 |
|---|---|---|
| `--contract` | 自動選擇最新 Pact | 指定要驗證的 Pact JSON。 |
| `--provider-url` | `http://localhost:8082` | RED 階段用來確認 Provider 尚未就緒的 URL。 |
| `--report` | `scratch/phase1_tdd_report.json` | JSON 測試報告輸出位置。 |

### 2.4 預期終端機結果

RED 階段若 Provider 尚未啟動，預期看到類似訊息：

```text
[TDD RED LIGHT] ... 成功捕捉連線拒絕/未啟動錯誤！
```

GREEN 階段若 Pact DSL、HTTP status 與 response body 都符合契約，預期看到：

```text
[TDD GREEN LIGHT] 🟢 Pact DSL 斷言成功！Mock Server 已精準回傳符合契約 Schema 之回應。
```

程式成功結束時的 exit code 為 `0`，條件是 RED 與 GREEN 都通過。若契約格式錯誤、套件缺少、請求不符合契約或驗證失敗，exit code 為 `1`。

### 2.5 預期產物

預設會產生：

```text
scratch/phase1_tdd_report.json
```

報告包含：

- `consumer`：Pact consumer 名稱
- `provider`：Pact provider 名稱
- `contract_path`：使用的 Pact 路徑
- `red_light.status`：RED 階段總結果
- `red_light.interactions`：每個 interaction 的請求與實際結果
- `green_light.status`：GREEN 階段總結果
- `green_light.interactions`：每個 interaction 的 status、response body 與失敗原因
- `duration_ms`：完整執行時間

可用 PowerShell 檢視結果：

```powershell
Get-Content .\scratch\phase1_tdd_report.json
```

判定成功的條件：

```text
red_light.status   = Pass
green_light.status = Pass
```

## 3. Mock Docker sandbox

### 3.1 使用目的

`generate_docker_mock.py` 會自動產生一個不需要啟動真實 Spring Boot Provider 的測試環境：

```text
Karate runner -> Pact stub server -> Pact contract
```

它會：

1. 讀取 `treesitter/karate_feature/` 下的 `.feature` 檔案。
2. 根據 feature 檔名推導 Provider 名稱。
3. 從 `treesitter/pact_contract/` 尋找對應 Pact contract。
4. 若主要契約目錄找不到，改從 `docker_sandbox_mock/tests/pact/` 尋找備援契約。
5. 只保留有對應 Pact contract 的 feature；找不到契約的 feature 會顯示略過訊息，不會阻止其他配對項目執行。
6. 將 Karate 內的 localhost、127.0.0.1 或其他 HTTP host 改寫成對應的 Pact stub service host。
7. 產生 Docker Compose、Karate runner Dockerfile 與 Pact contract 檔案。

如果只有一份 `*_api_test.feature` 與一份對應的 `*_contract.json`，sandbox 只會測試這一組。若完全沒有任何可配對的 feature/contract，程式會停止並顯示錯誤。

特殊映射：

```text
spring-petclinic-main -> pet-service
```

其他 feature 則依檔案名稱推導 Provider，例如：

```text
vet-service_api_test.feature -> vet-service
```

### 3.2 產生 sandbox

在專案根目錄執行：

```powershell
python .\treesitter\generate_docker_mock.py --clean
```

`--clean` 會刪除既有的 `docker_sandbox_mock/` 後重新產生。若不使用 `--clean`，而輸出目錄已存在且不是空目錄，程式會停止以避免覆蓋既有內容。

若要在生成後立即執行 Docker 測試：

```powershell
python .\treesitter\generate_docker_mock.py --clean --run
```

若測試完成後自動移除容器、網路與 volume：

```powershell
python .\treesitter\generate_docker_mock.py --clean --run --auto-down
```

### 3.3 自訂輸入與輸出位置

```powershell
python .\treesitter\generate_docker_mock.py `
  --karate-dir .\treesitter\karate_feature `
  --pact-dir .\treesitter\pact_contract `
  --fallback-pact-dir .\docker_sandbox_mock\tests\pact `
  --output .\docker_sandbox_mock `
  --clean
```

可用參數：

| 參數 | 預設值 | 說明 |
|---|---|---|
| `--karate-dir` | `treesitter/karate_feature` | Karate feature 輸入目錄。 |
| `--pact-dir` | `treesitter/pact_contract` | 主要 Pact contract 輸入目錄。 |
| `--fallback-pact-dir` | `docker_sandbox_mock/tests/pact` | 找不到主要契約時使用的備援目錄。 |
| `--output` | `docker_sandbox_mock` | sandbox 輸出目錄。 |
| `--clean` | 未啟用 | 清除並重建輸出目錄。 |
| `--run` | 未啟用 | 生成完成後執行 `docker compose up --build --exit-code-from karate-runner`。 |
| `--auto-down` | 未啟用 | 測試與報告完成後執行 `docker compose down -v`。 |
| `--report-path` | `scratch/phase2_docker_report.json` | 指定階段二 Docker 報告輸出位置。 |

### 3.4 預期產物

成功產生後，目錄結構類似：

```text
docker_sandbox_mock/
├── tests/
│   ├── karate/
│   │   └── *.feature
│   ├── pact/
│   │   └── *_contract*.json
│   └── Dockerfile.karate
├── test_reports/
│   └── karate/
├── docker-compose.yml
└── .gitignore
```

終端機會列出類似結果：

```text
Generated Docker mock sandbox: ...\docker_sandbox_mock
Karate features: <feature 數量>
Pact providers: <provider 名稱列表>
```

### 3.5 啟動 Docker 測試

推薦由腳本自動生成並執行：

```powershell
python .\treesitter\generate_docker_mock.py --clean --run
```

腳本會執行：

```text
docker compose -f <output_dir>/docker-compose.yml up --build --exit-code-from karate-runner
```

`karate-runner` 執行前會等待 3 秒，讓 Pact stub server 完成啟動。Exit Code 為 `0` 時，階段二結果為 `Pass`；其他 Exit Code 或 Docker 啟動例外則為 `Fail`。

也可以只生成 sandbox，再手動啟動：

```powershell
Set-Location .\docker_sandbox_mock
docker compose up --build --abort-on-container-exit
```

測試結束後停止並移除容器：

```powershell
docker compose down
```

若需要連同 volume 與孤立容器一起清除：

```powershell
docker compose down --volumes --remove-orphans
```

### 3.6 預期測試結果

Docker Compose 會啟動：

- 每個 Provider 一個 Pact stub server，例如 `vet-service-stub`。
- 一個 `karate-runner` 容器，執行所有產生的 Karate feature。

成功時應符合以下結果：

- Pact stub server 能依 Pact JSON 回應 API。
- Karate runner 能連線到對應的 `*-stub:8080`。
- Karate 測試符合 Pact contract 中定義的 status、headers 與 body。
- 測試報告寫入 `docker_sandbox_mock/test_reports/karate/`。

主要報告位置：

```text
docker_sandbox_mock/test_reports/karate/karate_console.txt
docker_sandbox_mock/test_reports/karate/karate-reports*/karate-summary.html
```

可用 PowerShell 檢視 console log：

```powershell
Get-Content .\test_reports\karate\karate_console.txt
```

階段二門禁報告預設位置：

```text
scratch/phase2_docker_report.json
```

報告包含：

- `timestamp`：ISO 8601 執行時間。
- `karate_features_count`：實際納入測試的 feature 數量。
- `providers`：實際啟動的 Provider 清單。
- `docker_exit_code`：`karate-runner` 的 Docker Exit Code；Docker 無法啟動時為 `null`。
- `status`：`Pass` 或 `Fail`。
- `report_console_path`：Karate console 報告路徑。

若使用 `--auto-down`，報告寫入完成後才會執行：

```powershell
docker compose -f <output_dir>\docker-compose.yml down -v
```

## 4. 建議測試順序

建議先執行單一 Pact 的 TDD 驗證，再建立完整 Mock Docker sandbox：

```powershell
Set-Location C:\Users\User\Desktop\2026SOSELab\yi\TeDMMAv2

python .\treesitter\generate_pact_tdd.py `
  --contract .\treesitter\pact_contract\vet-service_contract.json

python .\treesitter\generate_docker_mock.py --clean --run --auto-down

Get-Content .\scratch\phase2_docker_report.json
```

判讀方式：

| 階段 | 成功條件 | 主要成果 |
|---|---|---|
| Pact RED | 未啟動 Provider 回傳 connection refused 或 404 | 驗證尚未實作或尚未啟動時能被辨識。 |
| Pact GREEN | Pact mock 回應符合 status 與 body | `scratch/phase1_tdd_report.json`。 |
| Mock sandbox 產生 | feature 與 Pact 都能找到且格式正確 | `docker_sandbox_mock/`。 |
| Mock sandbox 執行 | `karate-runner` Exit Code 為 `0` | `scratch/phase2_docker_report.json` 與 `test_reports/karate/` 下的 log、HTML 報告。 |

## 5. 常見問題

### 找不到 Pact contract

確認 feature 推導出的 Provider 名稱與 Pact 檔案名稱一致，例如：

```text
vet-service_api_test.feature
vet-service_contract.json
```

必要時使用 `--pact-dir` 或 `--fallback-pact-dir` 指定正確目錄。

若同時存在多份 feature，但只有部分 feature 有契約，程式會略過無對應契約的 feature，只測試可配對的項目。例如：

```text
spring-petclinic-main_api_test.feature  -> 略過，找不到 pet-service_contract*.json
vet-service_api_test.feature             -> 執行，找到 vet-service_contract.json
```

### 輸出目錄不是空的

使用：

```powershell
python .\treesitter\generate_docker_mock.py --clean
```

### GREEN 階段缺少 Pact Python

安裝新版 `pact-python`：

```powershell
python -m pip install -U pact-python
```

### Docker 測試失敗

先查看：

```powershell
docker compose ps -a
docker compose logs
Get-Content .\test_reports\karate\karate_console.txt
```

常見原因包括 Pact interaction 與 Karate request 不一致、契約內 response body 不符合實際比對結果，或 Docker Desktop 尚未啟動。
