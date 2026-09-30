# TeDMMA

TeDMMA 是一套將單體 Java/Spring Boot 專案分析成微服務測試素材的工具。系統會讀取 Java 原始碼與既有測試，透過 Tree-sitter、RAG 與 LLM 產生微服務 API 測試、Karate feature 與 Pact contract，並提供 Docker sandbox 進行驗證。

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
Docker sandbox -> 驗證 API 行為、契約與服務容器環境
```

## 專案結構

***斜體為執行程式後生成之檔案***

~~刪除線為需要自行放入之檔案~~

### 根目錄

| 路徑 | 用途 |
|---|---|
| `requirements.txt` | 安裝所需 Python 套件。 |
| `treesitter/` | 主要 Python 程式、分析結果、知識庫與生成測試。 |
| ***`docker_sandbox_mock/`*** | 由 Pact stub server 與 Karate runner 組成的 Mock Docker sandbox。 |

### `treesitter/` 程式檔案

| 檔案 | 用途 |
|---|---|
| `generate_docker_sandbox.py` | 自動複製 Java 專案、Karate、Pact，並產生完整 Docker sandbox。 |
| `generate_docker_mock.py` | 自動產生 Pact stub server 與 Karate runner 的純 Mock sandbox。 |
| `generate_docker_contract.py` | 產生 Karate 行為測試、Pact Provider verification 與 Pact JSON lint sandbox。 |
| `generate_pact_tdd.py` | 針對單一 Pact contract 執行 RED/GREEN 驗證並產生報告。 |
| `generate_rag_backfill.py` | 將通過前置驗證的 Karate 與 Pact 素材整理並回填至 RAG 知識庫。 |
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

## 快速開始

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

### 3. 產生微服務測試素材

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

## 目前狀態

- 目前輸入專案為 `spring-petclinic-main`，位於 `treesitter/temp_project_source/`。
- 分析結果、生成的 feature 與 Pact contract 分別保存在 `treesitter/monolith_features/`、`treesitter/karate_feature/` 與 `treesitter/pact_contract/`。
- `docker_sandbox_mock/` 已包含一份可執行的 Mock sandbox 範例，以及 Karate 測試報告目錄。
- 完整 Spring Boot sandbox 生成器仍保留在 `treesitter/generate_docker_sandbox.py`，實際執行時會依輸入專案與輸出目錄產生 sandbox。

## 注意事項

- 分析與 RAG 流程需要有效的 LLM API key；請勿將金鑰提交至版本控制。
- 若 Spring Boot 專案使用 MongoDB，執行真實服務驗證時需要準備有效的連線設定。
- 生成的 Karate 與 Pact 素材應以目前 Java Controller、資料模型與預期微服務端點定義為準。
