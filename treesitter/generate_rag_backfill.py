from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PHASE1_REPORT = WORKSPACE_ROOT / "scratch" / "phase1_tdd_report.json"
DEFAULT_PHASE2_REPORT = WORKSPACE_ROOT / "scratch" / "phase2_docker_report.json"
DEFAULT_REPORT = WORKSPACE_ROOT / "scratch" / "phase3_rag_report.json"
KARATE_SOURCE_ROOT = WORKSPACE_ROOT / "treesitter" / "karate_feature"
PACT_SOURCE_ROOT = WORKSPACE_ROOT / "treesitter" / "pact_contract"
RAG_ROOT = WORKSPACE_ROOT / "treesitter" / "rag_knowledge_base"


@dataclass(frozen=True)
class GatekeeperResult:
    phase1_status: str
    phase2_status: str
    passed: bool


@dataclass(frozen=True)
class RelocatedFile:
    kind: str
    source: str
    target: str


@dataclass(frozen=True)
class BackfillConfig:
    project_name: str
    service_name: str
    phase1_report: Path
    phase2_report: Path
    report: Path
    force: bool
    es_host: str


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except OSError as error:
        raise RuntimeError(f"無法讀取報告 {path}: {error}") from error
    except json.JSONDecodeError as error:
        raise RuntimeError(f"報告不是有效 JSON {path}: {error}") from error
    if not isinstance(value, dict):
        raise RuntimeError(f"報告根節點必須是 JSON object: {path}")
    return value


def phase1_passed(report: dict[str, Any]) -> bool:
    return (
        isinstance(report.get("red_light"), dict)
        and report["red_light"].get("status") == "Pass"
        and isinstance(report.get("green_light"), dict)
        and report["green_light"].get("status") == "Pass"
    )


def phase2_passed(report: dict[str, Any]) -> bool:
    return report.get("status") == "Pass" and report.get("docker_exit_code") == 0


def check_gatekeeper(config: BackfillConfig) -> GatekeeperResult:
    try:
        phase1 = load_json(config.phase1_report)
        phase1_ok = phase1_passed(phase1)
    except RuntimeError as error:
        print(f"[GATEKEEPER CHECK] Warning: {error}")
        phase1_ok = False

    try:
        phase2 = load_json(config.phase2_report)
        phase2_ok = phase2_passed(phase2)
    except RuntimeError as error:
        print(f"[GATEKEEPER CHECK] Warning: {error}")
        phase2_ok = False

    result = GatekeeperResult(
        phase1_status="Pass" if phase1_ok else "Fail",
        phase2_status="Pass" if phase2_ok else "Fail",
        passed=phase1_ok and phase2_ok,
    )
    if result.passed:
        print("[GATEKEEPER CHECK] 🟢 Phase 1 & Phase 2 測試報告均為 Pass，准予進行資產歸位與 RAG 回填。")
    return result


def find_asset(root: Path, service_name: str, suffix: str) -> Path:
    patterns = [
        f"{service_name}{suffix}",
        f"{service_name}{suffix[:-len(Path(suffix).suffix)]}*.{Path(suffix).suffix.lstrip('.')}",
    ]
    candidates: list[Path] = []
    for pattern in patterns:
        candidates.extend(path for path in root.rglob(pattern) if path.is_file())
    if not candidates:
        raise FileNotFoundError(
            f"找不到 {service_name} 測試資產，搜尋位置: {root}"
        )
    return max(set(candidates), key=lambda path: path.stat().st_mtime)


def normalize_feature(path: Path) -> str:
    text = path.read_text(encoding="utf-8-sig")
    block = re.search(
        r"```(?:gherkin|karate|feature)\s*\n(.*?)\n```",
        text,
        re.DOTALL | re.IGNORECASE,
    )
    if block:
        text = block.group(1)
    else:
        feature_start = text.find("Feature:")
        if feature_start >= 0:
            text = text[feature_start:]
        text = re.sub(r"\s*```\s*$", "", text)
    if not re.search(r"^Feature:\s*", text, re.MULTILINE):
        raise ValueError(f"Karate 檔案缺少 Feature 宣告: {path}")
    return text.rstrip() + "\n"


def normalize_pact(path: Path) -> str:
    text = path.read_text(encoding="utf-8-sig")
    block = re.search(r"```(?:json)?\s*\n(.*?)\n```", text, re.DOTALL | re.IGNORECASE)
    if block:
        text = block.group(1)
    lines = text.splitlines()
    while lines and re.match(r"^\s*(//|#)", lines[0]):
        lines.pop(0)
    try:
        pact = json.loads("\n".join(lines))
    except json.JSONDecodeError as error:
        raise ValueError(f"Pact JSON 無效: {path}: {error}") from error
    if not isinstance(pact, dict) or not pact.get("provider", {}).get("name"):
        raise ValueError(f"Pact 缺少 provider.name: {path}")
    return json.dumps(pact, indent=2, ensure_ascii=False) + "\n"


def relocate_asset(source: Path, targets: list[Path], content: str, kind: str) -> list[RelocatedFile]:
    relocated: list[RelocatedFile] = []
    for target_dir in targets:
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / source.name
        target.write_text(content, encoding="utf-8")
        relocated.append(
            RelocatedFile(kind=kind, source=str(source.resolve()), target=str(target.resolve()))
        )
    return relocated


def relocate_assets(config: BackfillConfig) -> tuple[list[RelocatedFile], list[Path]]:
    karate_source = find_asset(KARATE_SOURCE_ROOT, config.service_name, "_api_test.feature")
    pact_source = find_asset(PACT_SOURCE_ROOT, config.service_name, "_contract.json")
    karate_content = normalize_feature(karate_source)
    pact_content = normalize_pact(pact_source)
    karate_targets = [
        KARATE_SOURCE_ROOT / config.project_name,
        RAG_ROOT / "karate",
    ]
    pact_targets = [
        PACT_SOURCE_ROOT / config.project_name,
        RAG_ROOT / "pact" / "v3" / "pass",
    ]
    relocated = relocate_asset(karate_source, karate_targets, karate_content, "karate")
    relocated.extend(relocate_asset(pact_source, pact_targets, pact_content, "pact"))
    return relocated, [path for path in (karate_targets + pact_targets)]


def backfill_elasticsearch(
    config: BackfillConfig, files: list[RelocatedFile]
) -> tuple[int, str, str | None]:
    index_name = f"{config.project_name}_migration_docs"
    try:
        from haystack import Document
        from haystack.document_stores.types import DuplicatePolicy
        from haystack_integrations.components.embedders.sentence_transformers import (
            SentenceTransformersDocumentEmbedder,
        )
        from haystack_integrations.document_stores.elasticsearch import ElasticsearchDocumentStore

        documents = []
        for item in files:
            path = Path(item.target)
            category = "karate_specification" if item.kind == "karate" else "pact_passed_example"
            documents.append(
                Document(
                    content=path.read_text(encoding="utf-8"),
                    meta={
                        "file_name": str(path),
                        "category": category,
                        "service": config.service_name,
                    },
                )
            )

        embedder = SentenceTransformersDocumentEmbedder(
            model="sentence-transformers/all-MiniLM-L6-v2"
        )
        embedder.warm_up()
        embedded_documents = embedder.run(documents=documents)["documents"]
        document_store = ElasticsearchDocumentStore(hosts=config.es_host, index=index_name)
        document_store.write_documents(embedded_documents, policy=DuplicatePolicy.OVERWRITE)
        return len(embedded_documents), "Pass", None
    except Exception as error:  # ES and model setup are optional for file relocation.
        warning = f"Elasticsearch 回填失敗，檔案歸位已完成: {error}"
        print(f"[RAG BACKFILL WARNING] ⚠️ {warning}")
        return 0, "Fail", warning


def write_report(
    config: BackfillConfig,
    gatekeeper: GatekeeperResult,
    relocated: list[RelocatedFile],
    indexed_count: int,
    elasticsearch_status: str,
    error: str | None = None,
    status: str | None = None,
) -> None:
    report = {
        "timestamp": datetime.now(timezone.utc).astimezone().isoformat(),
        "service_name": config.service_name,
        "gatekeeper": asdict(gatekeeper),
        "relocated_files": [asdict(item) for item in relocated],
        "elasticsearch": {
            "index": f"{config.project_name}_migration_docs",
            "indexed_documents_count": indexed_count,
            "status": elasticsearch_status,
        },
        "status": status or ("Pass" if elasticsearch_status == "Pass" else "Fail"),
    }
    if error:
        report["error"] = error
    config.report.parent.mkdir(parents=True, exist_ok=True)
    config.report.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def run(config: BackfillConfig) -> int:
    gatekeeper = check_gatekeeper(config)
    if not gatekeeper.passed and not config.force:
        message = "[RAG BACKFILL BLOCKED] ⛔ 前置階段測試未完全通過，中斷 RAG 回填作業！"
        print(message)
        write_report(config, gatekeeper, [], 0, "Fail", message, "Blocked")
        return 1

    if not gatekeeper.passed:
        print("[GATEKEEPER CHECK] ⚠️ 門禁未通過，但已使用 --force，繼續執行回填。")

    relocated: list[RelocatedFile] = []
    try:
        relocated, _ = relocate_assets(config)
        indexed_count, elasticsearch_status, error = backfill_elasticsearch(config, relocated)
    except (FileNotFoundError, OSError, ValueError, RuntimeError) as exception:
        error = str(exception)
        print(f"[RAG BACKFILL FAILED] ❌ {error}")
        write_report(config, gatekeeper, relocated, 0, "Fail", error, "Fail")
        return 1

    write_report(config, gatekeeper, relocated, indexed_count, elasticsearch_status, error)
    if elasticsearch_status == "Pass":
        print(
            f"[RAG BACKFILL COMPLETE] 🚀 已成功將 {indexed_count} 筆測試資產寫入 Elasticsearch "
            f"({config.project_name}_migration_docs)，完成 N+1 服務之檢索準備！"
        )
        return 0
    return 1


def parse_args() -> BackfillConfig:
    parser = argparse.ArgumentParser(description="Run Phase 3 asset relocation and RAG backfill.")
    parser.add_argument("--project-name", default="spring-petclinic-main")
    parser.add_argument("--service-name", required=True)
    parser.add_argument("--phase1-report", type=Path, default=DEFAULT_PHASE1_REPORT)
    parser.add_argument("--phase2-report", type=Path, default=DEFAULT_PHASE2_REPORT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--force", action="store_true", help="Ignore failed phase gates.")
    parser.add_argument("--es-host", default="http://localhost:9200")
    arguments = parser.parse_args()
    return BackfillConfig(
        project_name=arguments.project_name,
        service_name=arguments.service_name,
        phase1_report=arguments.phase1_report,
        phase2_report=arguments.phase2_report,
        report=arguments.report,
        force=arguments.force,
        es_host=arguments.es_host,
    )


if __name__ == "__main__":
    sys.exit(run(parse_args()))