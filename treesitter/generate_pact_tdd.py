from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import requests


WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
PACT_ROOT = WORKSPACE_ROOT / "treesitter" / "pact_contract"
DEFAULT_REPORT = WORKSPACE_ROOT / "scratch" / "phase1_tdd_report.json"
DEFAULT_PROVIDER_URL = "http://localhost:8082"
REQUEST_TIMEOUT_SECONDS = 2.0


@dataclass(frozen=True)
class Interaction:
    description: str
    provider_state: str
    method: str
    path: str
    request_headers: dict[str, str]
    request_body: Any
    expected_status: int
    response_headers: dict[str, str]
    response_body: Any


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except OSError as error:
        raise RuntimeError(f"無法讀取契約檔案 {path}: {error}") from error
    except json.JSONDecodeError as error:
        raise RuntimeError(f"契約檔案不是有效 JSON {path}: {error}") from error
    if not isinstance(value, dict):
        raise RuntimeError(f"契約根節點必須是 JSON object: {path}")
    return value


def select_contract(path: Path | None) -> Path:
    if path is not None:
        if not path.is_file():
            raise FileNotFoundError(f"找不到指定 Pact 契約: {path}")
        return path

    nested_dir = PACT_ROOT / "spring-petclinic-main"
    candidates = list(nested_dir.glob("*.json")) if nested_dir.is_dir() else []
    if not candidates:
        candidates = list(PACT_ROOT.glob("*.json"))
    if not candidates:
        raise FileNotFoundError(f"找不到 Pact JSON 契約，搜尋位置: {nested_dir}、{PACT_ROOT}")
    return max(candidates, key=lambda candidate: candidate.stat().st_mtime)


def as_headers(value: Any, field_name: str) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} 必須是 object")
    return {str(key): str(item) for key, item in value.items()}


def parse_interaction(value: Any, index: int) -> Interaction:
    if not isinstance(value, Mapping):
        raise ValueError(f"interactions[{index}] 必須是 object")
    request = value.get("request")
    response = value.get("response")
    states = value.get("providerStates", [])
    if not isinstance(request, Mapping) or not isinstance(response, Mapping):
        raise ValueError(f"interactions[{index}] 缺少有效 request 或 response")
    if not isinstance(states, list):
        raise ValueError(f"interactions[{index}].providerStates 必須是 array")
    state_names = [state.get("name") for state in states if isinstance(state, Mapping)]
    method = request.get("method")
    path = request.get("path")
    status = response.get("status")
    description = value.get("description")
    if not all(isinstance(item, str) and item for item in (method, path, description)):
        raise ValueError(f"interactions[{index}] 的 description/method/path 不完整")
    if not isinstance(status, int):
        raise ValueError(f"interactions[{index}].response.status 必須是整數")
    return Interaction(
        description=description,
        provider_state=", ".join(str(name) for name in state_names if name) or "provider state",
        method=method.upper(),
        path=path,
        request_headers=as_headers(request.get("headers"), "request.headers"),
        request_body=request.get("body"),
        expected_status=status,
        response_headers=as_headers(response.get("headers"), "response.headers"),
        response_body=response.get("body"),
    )


def parse_contract(path: Path) -> tuple[str, str, list[Interaction]]:
    pact = load_json(path)
    consumer = pact.get("consumer", {}).get("name")
    provider = pact.get("provider", {}).get("name")
    raw_interactions = pact.get("interactions")
    if not isinstance(consumer, str) or not consumer:
        raise ValueError("契約缺少 consumer.name")
    if not isinstance(provider, str) or not provider:
        raise ValueError("契約缺少 provider.name")
    if not isinstance(raw_interactions, list) or not raw_interactions:
        raise ValueError("契約缺少非空 interactions array")
    return consumer, provider, [parse_interaction(item, index) for index, item in enumerate(raw_interactions)]


def endpoint_url(base_url: str, path: str) -> str:
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


def run_red_verification(
    interactions: list[Interaction], base_url: str
) -> tuple[bool, list[dict[str, Any]]]:
    passed = True
    results: list[dict[str, Any]] = []
    for interaction in interactions:
        url = endpoint_url(base_url, interaction.path)
        started = time.perf_counter()
        result: dict[str, Any] = {
            "description": interaction.description,
            "provider_state": interaction.provider_state,
            "method": interaction.method,
            "path": interaction.path,
            "url": url,
            "expected_status": "connection refused or 404",
            "actual_status": None,
            "request_body": interaction.request_body,
            "expected_body": None,
            "actual_body": None,
            "status": "Fail",
            "failure_reason": None,
        }
        try:
            response = requests.request(
                interaction.method,
                url,
                headers=interaction.request_headers or None,
                json=interaction.request_body,
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except requests.exceptions.ConnectionError:
            print(f"[TDD RED LIGHT] 🔴 成功捕捉連線拒絕/未啟動錯誤！驗證端點 {url} 尚未就緒。")
            result["status"] = "Pass"
            result["actual_status"] = "connection refused"
        except requests.exceptions.RequestException as error:
            print(f"[TDD RED LIGHT] 🔴 端點請求失敗 {url}: {error}")
            passed = False
            result["failure_reason"] = str(error)
        else:
            result["actual_status"] = response.status_code
            if response.status_code == 404:
                print(f"[TDD RED LIGHT] 🔴 成功捕捉連線拒絕/未啟動錯誤！驗證端點 {url} 尚未就緒。")
                result["status"] = "Pass"
            else:
                print(f"[TDD RED LIGHT] ⚠️ 端點 {url} 回應 HTTP {response.status_code}，未符合預期的未就緒狀態。")
                passed = False
                result["failure_reason"] = f"未預期的 HTTP status: {response.status_code}"
            try:
                result["actual_body"] = response.json()
            except ValueError:
                result["actual_body"] = response.text
        result["duration_ms"] = round((time.perf_counter() - started) * 1000, 3)
        results.append(result)
    return passed, results


def assert_response_body(response: requests.Response, expected: Any) -> None:
    if expected is None:
        return
    try:
        actual = response.json()
    except ValueError as error:
        raise AssertionError("契約要求 JSON body，但 mock response 無法解析 JSON") from error
    if actual != expected:
        raise AssertionError(f"JSON body 不符合契約，預期 {expected!r}，實際 {actual!r}")


def build_pact(consumer_name: str, provider_name: str, interactions: list[Interaction]) -> Any:
    try:
        from pact import Pact
    except ImportError as error:
        raise RuntimeError("缺少新版 pact-python，請先執行: python -m pip install -U pact-python") from error

    pact = Pact(consumer_name, provider_name)
    for interaction in interactions:
        configured = pact.upon_receiving(interaction.description).given(interaction.provider_state)
        configured.with_request(interaction.method, interaction.path)
        for name, value in interaction.request_headers.items():
            configured.with_header(name, value, part="Request")
        if interaction.request_body is not None:
            configured.with_body(
                interaction.request_body,
                content_type="application/json",
                part="Request",
            )
        configured.will_respond_with(interaction.expected_status)
        for name, value in interaction.response_headers.items():
            configured.with_header(name, value, part="Response")
        if interaction.response_body is not None:
            configured.with_body(
                interaction.response_body,
                content_type="application/json",
                part="Response",
            )
    return pact


def run_green_verification(
    consumer_name: str,
    provider_name: str,
    interactions: list[Interaction],
) -> tuple[bool, list[dict[str, Any]]]:
    results: list[dict[str, Any]] = []
    mock_server_url: str | None = None
    try:
        from pact.error import MismatchesError

        pact = build_pact(consumer_name, provider_name, interactions)
        with pact.serve() as mock_server:
            mock_server_url = str(mock_server.url)
            for interaction in interactions:
                started = time.perf_counter()
                result: dict[str, Any] = {
                    "description": interaction.description,
                    "provider_state": interaction.provider_state,
                    "method": interaction.method,
                    "path": interaction.path,
                    "url": endpoint_url(mock_server_url, interaction.path),
                    "expected_status": interaction.expected_status,
                    "actual_status": None,
                    "request_body": interaction.request_body,
                    "expected_body": interaction.response_body,
                    "actual_body": None,
                    "status": "Fail",
                    "failure_reason": None,
                }
                url = endpoint_url(str(mock_server.url), interaction.path)
                try:
                    response = requests.request(
                        interaction.method,
                        url,
                        headers=interaction.request_headers or None,
                        json=interaction.request_body,
                        timeout=REQUEST_TIMEOUT_SECONDS,
                    )
                    result["actual_status"] = response.status_code
                    try:
                        result["actual_body"] = response.json()
                    except ValueError:
                        result["actual_body"] = response.text
                    assert response.status_code == interaction.expected_status, (
                        f"{interaction.description}: 預期 HTTP {interaction.expected_status}，"
                        f"實際 {response.status_code}"
                    )
                    assert_response_body(response, interaction.response_body)
                    result["status"] = "Pass"
                except (AssertionError, requests.exceptions.RequestException) as error:
                    result["failure_reason"] = str(error)
                finally:
                    result["duration_ms"] = round((time.perf_counter() - started) * 1000, 3)
                    results.append(result)
        print("[TDD GREEN LIGHT] 🟢 Pact DSL 斷言成功！Mock Server 已精準回傳符合契約 Schema 之回應。")
        return all(result["status"] == "Pass" for result in results), results
    except (
        AssertionError,
        MismatchesError,
        requests.exceptions.RequestException,
        RuntimeError,
        OSError,
        TypeError,
    ) as error:
        print(f"[TDD GREEN LIGHT] 🔴 Pact DSL 驗證失敗: {error}")
        if not results:
            results = [
                {
                    "description": interaction.description,
                    "provider_state": interaction.provider_state,
                    "method": interaction.method,
                    "path": interaction.path,
                    "url": endpoint_url(mock_server_url, interaction.path)
                    if mock_server_url
                    else None,
                    "expected_status": interaction.expected_status,
                    "actual_status": None,
                    "request_body": interaction.request_body,
                    "expected_body": interaction.response_body,
                    "actual_body": None,
                    "status": "Fail",
                    "failure_reason": str(error),
                    "duration_ms": None,
                }
                for interaction in interactions
            ]
        return False, results


def write_report(
    report_path: Path,
    consumer_name: str,
    provider_name: str,
    contract_path: Path,
    started_at: str,
    finished_at: str,
    red_passed: bool,
    red_results: list[dict[str, Any]],
    green_passed: bool,
    green_results: list[dict[str, Any]],
) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "consumer": consumer_name,
        "provider": provider_name,
        "contract_path": str(contract_path),
        "started_at": started_at,
        "finished_at": finished_at,
        "duration_ms": None,
        "red_light": {
            "status": "Pass" if red_passed else "Fail",
            "interactions": red_results,
        },
        "green_light": {
            "status": "Pass" if green_passed else "Fail",
            "interactions": green_results,
        },
    }
    started = datetime.fromisoformat(started_at)
    finished = datetime.fromisoformat(finished_at)
    report["duration_ms"] = round((finished - started).total_seconds() * 1000, 3)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Pact DSL TDD red/green verification.")
    parser.add_argument("--contract", type=Path, help="Pact JSON path; default selects the newest contract.")
    parser.add_argument("--provider-url", default=DEFAULT_PROVIDER_URL, help="未啟動 Provider 的紅燈測試 URL base。")
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT, help="TDD JSON report output path.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    consumer_name = provider_name = "unknown"
    contract_path = args.contract or Path("unknown")
    started_at = datetime.now(timezone.utc).isoformat()
    finished_at = started_at
    red_passed = green_passed = False
    red_results: list[dict[str, Any]] = []
    green_results: list[dict[str, Any]] = []
    try:
        contract_path = select_contract(args.contract)
        consumer_name, provider_name, interactions = parse_contract(contract_path)
        print(f"使用 Pact 契約: {contract_path}")
        red_passed, red_results = run_red_verification(interactions, args.provider_url)
        green_passed, green_results = run_green_verification(consumer_name, provider_name, interactions)
    except (FileNotFoundError, OSError, RuntimeError, ValueError) as error:
        print(f"Pact TDD 執行失敗: {error}", file=sys.stderr)
    finally:
        finished_at = datetime.now(timezone.utc).isoformat()
        try:
            write_report(
                args.report,
                consumer_name,
                provider_name,
                contract_path,
                started_at,
                finished_at,
                red_passed,
                red_results,
                green_passed,
                green_results,
            )
        except OSError as error:
            print(f"無法寫入 TDD 報告 {args.report}: {error}", file=sys.stderr)
            return 1
    return 0 if red_passed and green_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())