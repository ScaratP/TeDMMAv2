# TeDMMA

TeDMMA 是一套將單體 Java/Spring Boot 專案分析成微服務測試素材的工具。系統會讀取 Java 原始碼與既有測試，透過 Tree-sitter、RAG 與 LLM 產生微服務 API 測試，並以 Karate 與 Pact 進行契約與行為驗證。現在的主要測試方式是 Mock Docker sandbox，作為最直接、最快的驗證入口；完整 Docker sandbox 則作為備用的實體服務驗證路徑。

## 系統流程

```text
Java 原始碼
    |
    v
Tree-sitter 分析 -> 單體特徵、測試案例、LLM 架構分析
    |
    v
RAG + LLM -> Karate feature、Pact contract
    |
    v
Mock Docker sandbox -> 先驗證 API client / API 行為是否符合契約
    |
    v
Docker sandbox -> 進一步驗證真實 Spring Boot 服務與容器環境
```

## 專案結構

***斜體為執行程式後生成之檔案***

~~刪除線為需要自行放入之檔案~~

### 根目錄

| 路徑 | 用途 |
|---|---|
| `requirements.txt` | 安裝所需 Python 套件。 |
| `treesitter/` | 主要 Python 程式、分析結果、知識庫與生成測試。 |
| ***`docker_sandbox/`*** | 儲存完整的實體服務 Docker sandbox，適合真實 Spring Boot 服務與容器整合驗證。 |
| ***`docker_sandbox_mock`*** | 目前主要測試入口，使用 Pact stub server + Karate runner，直接模擬 API 契約與外部行為。 |

### `treesitter/` 程式檔案

| 檔案 | 用途 |
|---|---|
| `generate_docker_sandbox.py` | 自動複製 Java 專案、Karate、Pact，並產生完整 Docker sandbox。 |
| `generate_docker_mock.py` | 自動產生 Pact stub server 與 Karate runner 的純 Mock sandbox。 |
| `generate_docker_contract.py` | 產生 Karate 行為測試、Pact Provider verification 與 Pact JSON lint sandbox。 |
| `main.py` | 讀取 Java 專案並執行 Tree-sitter 特徵擷取與單體分析流程。 |
| `rag_migrate.py` | 使用 RAG/LLM 根據分析結果生成指定微服務的測試腳本。 |
| `feature_capture.py` | 擷取 Java 專案中的特徵與結構資訊。 |
| `api_test_generate.py` | 產生 API 測試相關內容。 |
| `text_processor.py` | 處理、整理與切分文字資料。 |
| `prettify_sexp.py` | 將 Tree-sitter S-expression 整理成較易讀格式。 |
| `test_ast_capture.py` | AST 擷取相關測試。 |
| ~~`api_key.txt`~~ | ~~LLM API key。~~ |

### `treesitter/` 輸入、知識庫與輸出

| 路徑 | 用途 |
|---|---|
| ***`temp_project_source/`*** | 放置待分析的 Java/Spring Boot 原始專案，自動解壓縮後的檔案。 |
| ***`rag_knowledge_base/karate/`*** | Karate 範例知識庫，供 RAG 檢索。 |
| ***`rag_knowledge_base/pact/`*** | Pact 範例知識庫，供 RAG 檢索。 |
| ~~`monolith_features/`~~ | 單體專案擷取出的元件、欄位與結構特徵。 |
| ~~`monolith_test_case_codes/`~~ | 單體專案既有測試程式碼。 |
| ~~`llm_analysis_result/`~~ | LLM 產生的微服務拆分與架構分析結果。 |
| ~~`expected_microservice_endpoint/`~~ | 預期微服務 API endpoint 定義。 |
| ~~`migrate_project/`~~ | 遷移流程使用的專案資料 zip 檔。 |
| `tree-sitter-venv/` | Tree-sitter 專用 Python 虛擬環境。 |

### 目前測試輸入

| 路徑 | 用途 |
|---|---|
| `treesitter/temp_project_source/spring-petclinic-main/` | 目前的 Java 專案來源。 |
| `treesitter/karate_feature/*.feature` | Karate API 測試。 |
| `treesitter/pact_contract/*.json` | Pact V3 合約。 |

## 環境需求

- Windows PowerShell
- Python 3
- Docker Desktop 與 Docker Compose
- Maven/Java 不必安裝在主機上，後端建置使用 Docker image
- 若執行分析與 RAG 流程，需要 Python 虛擬環境及有效的 LLM API key
- 若 Spring Boot 使用 MongoDB，需準備有效的 `MONGODB_URI`、`MONGODB_USERNAME` 與 `MONGODB_PASSWORD`

## 快速使用

### 1. 啟用 Python 虛擬環境

在專案根目錄執行：

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
.\.venv\Scripts\Activate.ps1
```

若使用 Tree-sitter 專用環境：

```powershell
.\treesitter\tree-sitter-venv\Scripts\Activate.ps1
```

安裝必要套件：

```powershell
pip install -r requirements.txt
```

### 2. 產生單體分析結果

```powershell
cd .\treesitter
python .\main.py
```

輸出檔案：

- `monolith_features/*_features.txt`
- `monolith_test_case_codes/*_test_cases.txt`
- `llm_analysis_result/*_analysis_response.txt`
- `karate_feature/*_api_test.feature`

### 3. 產生微服務測試

```powershell
python .\rag_migrate.py
```

依照終端機提示輸入微服務名稱，例如 `visit-service`，實際內容可參考 `treesitter\expected_microservice_endpoint\*_expected_microservice_endpoints.yaml`。產物放在：

```text
karate_feature/
└── <service>_api_test.feature
pact_contract/
└── <service>_contract.json
```

輸入 `Q` 可結束互動流程。

### 4. 主要測試方式：Mock Docker sandbox

這是目前最主要、最推薦的測試入口。它不需要啟動真實 Spring Boot 服務，而是由 Pact stub server 直接依契約提供 API mock，並讓 Karate runner 直接執行所有 feature。

```powershell
python .\treesitter\generate_docker_mock.py --clean
cd .\docker_sandbox_mock
docker compose up --build --abort-on-container-exit
```

### 4-1. 契約行為驗證 sandbox

若要驗證真實 Provider，而不是只呼叫 Pact stub，使用獨立的契約 sandbox：

```powershell
python .\treesitter\generate_docker_contract.py `
    --provider-url-template "http://host.docker.internal:8080" `
    --clean
cd .\docker_sandbox_contract
docker compose up --build --abort-on-container-exit
```

這個 sandbox 分成三個責任：

- `karate-runner`：呼叫真實 Provider，測試 API 行為與端點流程。
- `*-pact-verifier`：使用 Pact CLI 依每份 Pact 呼叫真實 Provider，驗證 Consumer-Provider 契約行為。
- `pact-lint`：使用 JSON Schema 檢查 Pact V3 結構、provider states 與必要欄位，不負責測試 API 行為。

每個 Provider 的 URL 可用 `{provider}` 動態指定，例如：

```powershell
python .\treesitter\generate_docker_contract.py `
    --provider-url-template "http://{provider}.internal:8080" `
    --clean
```

報告位置：

- `docker_sandbox_contract/test_reports/karate/karate_console.txt`
- `docker_sandbox_contract/test_reports/pact/<provider>_verification.txt`
- `docker_sandbox_contract/test_reports/pact/lint.txt`


### 4-2. 備用方案：完整 Docker sandbox
驗證真實 Spring Boot 服務在容器中的啟動、健康檢查、端點與運行狀態。

```powershell
python .\treesitter\generate_docker_sandbox.py --clean
cd .\docker_sandbox
docker compose up --build --abort-on-container-exit
```

生成器會自動：

1. 找到來源目錄下真正包含 `pom.xml` 的 Maven 專案。
2. 複製 `src/`、`pom.xml` 與可選的 `.env`。
3. 複製 Karate 與 Pact 檔案，並依實際檔名更新 compose 指令。
4. 將 Karate 中的 `localhost` 或 `127.0.0.1` 改成 `http://visit-service:8080`。
5. 移除 Pact JSON 開頭的 `//` 或 `#` 註解後驗證 JSON 格式。
6. 產生兩個 Dockerfile、`docker-compose.yml` 與 `.gitignore`。

### 4-2-1. 使用不同來源檔案

```powershell
python .\treesitter\generate_docker_sandbox.py `
  --source .\treesitter\temp_project_source\another-project `
  --karate .\treesitter\karate_feature\another-api.feature `
  --pact .\treesitter\pact_contract\another-contract.json `
  --output .\docker_sandbox `
  --clean
```

`--clean` 會刪除並重建輸出目錄；不使用它時，若輸出目錄不是空的，程式會停止以避免意外覆蓋檔案。

## Mock Docker sandbox 結構

```text
docker_sandbox_mock/
├── tests/
│   ├── karate/              # Karate feature 檔案
│   ├── pact/                # Pact JSON 合約
│   └── Dockerfile.karate    # Karate 測試執行環境
├── test_reports/
│   ├── karate/              # Karate console、HTML 與 JSON 報告
│   └── pact/                # Pact console 報告
├── docker-compose.yml       # 啟動 mock 與測試容器
└── .gitignore               # 忽略暫存與報告輸出
```

## 執行結果

### Mock sandbox 成功啟動時

- Pact stub server 依契約提供 API mock。
- Karate tester 直接呼叫 mock endpoint。
- 測試輸出會寫入 `docker_sandbox_mock/test_reports/`。
- 若 API 契約與 feature 一致，則代表目前的微服務設計與測試腳本已對齊。

### Mock 報告位置

| 報告 | 路徑 | 說明 |
|---|---|---|
| Karate console | `docker_sandbox_mock/test_reports/karate/karate_console.txt` | Karate 執行紀錄與錯誤。 |
| Karate HTML | `docker_sandbox_mock/test_reports/karate/karate-reports*/karate-summary.html` | Karate 網頁報告。 |


### 常用清理指令

```powershell
cd .\docker_sandbox_mock
docker compose down
```

## 完整 Docker sandbox：

```powershell
cd .\docker_sandbox
docker compose down
```

移除測試容器、網路與 volume：

```powershell
docker compose down --volumes --remove-orphans
```

## 目前限制

- Pact provider state 目前沒有連接 provider-state setup API，因此 Pact 會略過 state 的自動資料準備；測試資料必須已存在於後端資料庫，或由服務啟動流程準備。
- `docker compose up --abort-on-container-exit` 在某個測試容器先結束時會停止其他測試容器；若需要完整 Karate HTML 報告，請單獨執行 Karate。
- 測試是否通過取決於目前 Java Controller、MongoDB 資料與生成的 Karate/Pact 合約是否一致。

## Docker sandbox（完整服務驗證）

以下為完整 Docker sandbox 的結構與使用說明，作為真實服務驗證的備用方案：

### Docker sandbox 結構

```text
docker_sandbox/
├── visit-service/
│   ├── src/                 # Spring Boot 原始碼
│   ├── pom.xml              # Maven 設定
│   ├── .env                 # 可選，MongoDB 等環境變數
│   └── Dockerfile           # 建置 Spring Boot 映像
├── tests/
│   ├── karate/              # Karate feature 檔案
│   ├── pact/                # Pact JSON 合約
│   └── Dockerfile.karate    # Karate 執行環境
├── test_reports/
│   ├── karate/              # Karate console、HTML 與 JSON 報告
│   └── pact/                # Pact console 報告
├── docker-compose.yml       # 啟動服務與測試容器
└── .gitignore               # 忽略 .env 與 test_reports/
```

### Docker sandbox 啟動方式

```powershell
cd .\docker_sandbox
docker compose up --build --abort-on-container-exit
```

若主機的 8080 已被其他程式使用，這份 sandbox 預設將主機的 `8081` 對應到容器的 `8080`：

```text
主機：http://localhost:8081
容器內：http://visit-service:8080
```

### Docker sandbox 執行結果

- `visit-service` 容器啟動 Spring Boot。
- Docker healthcheck 使用 `nc` 檢查 8080 port 是否開啟，不依賴 HTTP 200。
- Karate tester 連線到 `http://visit-service:8080`。
- Pact verifier 連線到同一個容器名稱與 port。
- 測試輸出會寫入 `test_reports/`，不只顯示在終端機。

### Docker sandbox 報告位置

| 報告 | 路徑 | 說明 |
|---|---|---|
| Karate console | `docker_sandbox/test_reports/karate/karate_console.txt` | Karate 執行紀錄與錯誤。 |
| Karate HTML | `docker_sandbox/test_reports/karate/karate-reports*/karate-summary.html` | Karate 網頁報告。 |
| Karate JSON/Log | `docker_sandbox/test_reports/karate/` | 測試明細與執行 log。 |
| Pact console | `docker_sandbox/test_reports/pact/pact_console.txt` | Pact 合約比對結果。 |

也可以單獨產生完整 Karate HTML 報告：

```powershell
docker compose up -d visit-service
docker compose run --rm karate-tester
docker compose down
```

### Docker sandbox 遇到錯誤時怎麼看

1. 先開啟 `test_reports/karate/karate_console.txt` 與 `test_reports/pact/pact_console.txt`。
2. 若服務未達健康狀態，先執行：

   ```powershell
   docker compose ps -a
   docker compose logs visit-service
   ```

3. 若看到 `404`，通常代表目標服務尚未實作測試要求的 endpoint，例如 `/sights`。
4. 若看到 MongoDB connection string 錯誤，檢查 `visit-service/.env` 是否存在，以及 `MONGODB_URI` 是否以 `mongodb://` 或 `mongodb+srv://` 開頭。
5. 若看到 port bind 錯誤，確認主機 8081 是否也被占用，再調整 compose 的主機端 port。
