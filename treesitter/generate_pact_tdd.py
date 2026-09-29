from __future__ import annotations

import argparse
import json
import sys
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


def run_red_verification(interactions: list[Interaction], base_url: str) -> bool:
    passed = True
    for interaction in interactions:
        url = endpoint_url(base_url, interaction.path)
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
            continue
        except requests.exceptions.RequestException as error:
            print(f"[TDD RED LIGHT] 🔴 端點請求失敗 {url}: {error}")
            passed = False
            continue
        if response.status_code == 404:
            print(f"[TDD RED LIGHT] 🔴 成功捕捉連線拒絕/未啟動錯誤！驗證端點 {url} 尚未就緒。")
        else:
            print(f"[TDD RED LIGHT] ⚠️ 端點 {url} 回應 HTTP {response.status_code}，未符合預期的未就緒狀態。")
            passed = False
    return passed


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
        from pact import Consumer, Provider
    except ImportError as error:
        raise RuntimeError("缺少 pact-python，請先執行: pip install pact-python") from error

    pact = Consumer(consumer_name).has_pact_with(Provider(provider_name))
    for interaction in interactions:
        configured = (
            pact.given(interaction.provider_state)
            .upon_receiving(interaction.description)
            .with_request(
                interaction.method,
                interaction.path,
                headers=interaction.request_headers or None,
            )
        )
        if interaction.request_body is not None:
            configured.with_body(interaction.request_body)
        configured.will_respond_with(
            interaction.expected_status,
            headers=interaction.response_headers or None,
            body=interaction.response_body,
        )
    return pact


def run_green_verification(
    consumer_name: str,
    provider_name: str,
    interactions: list[Interaction],
) -> bool:
    try:
        pact = build_pact(consumer_name, provider_name, interactions)
        with pact:
            for interaction in interactions:
                url = endpoint_url(pact.uri, interaction.path)
                response = requests.request(
                    interaction.method,
                    url,
                    headers=interaction.request_headers or None,
                    json=interaction.request_body,
                    timeout=REQUEST_TIMEOUT_SECONDS,
                )
                assert response.status_code == interaction.expected_status, (
                    f"{interaction.description}: 預期 HTTP {interaction.expected_status}，"
                    f"實際 {response.status_code}"
                )
                assert_response_body(response, interaction.response_body)
        print("[TDD GREEN LIGHT] 🟢 Pact DSL 斷言成功！Mock Server 已精準回傳符合契約 Schema 之回應。")
        return True
    except (AssertionError, requests.exceptions.RequestException, RuntimeError, OSError, TypeError) as error:
        print(f"[TDD GREEN LIGHT] 🔴 Pact DSL 驗證失敗: {error}")
        return False


def write_report(
    report_path: Path,
    consumer_name: str,
    provider_name: str,
    red_passed: bool,
    green_passed: bool,
) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "consumer": consumer_name,
        "provider": provider_name,
        "red_light": "Pass" if red_passed else "Fail",
        "green_light": "Pass" if green_passed else "Fail",
    }
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
    red_passed = green_passed = False
    try:
        contract_path = select_contract(args.contract)
        consumer_name, provider_name, interactions = parse_contract(contract_path)
        print(f"使用 Pact 契約: {contract_path}")
        red_passed = run_red_verification(interactions, args.provider_url)
        green_passed = run_green_verification(consumer_name, provider_name, interactions)
    except (FileNotFoundError, OSError, RuntimeError, ValueError) as error:
        print(f"Pact TDD 執行失敗: {error}", file=sys.stderr)
    finally:
        try:
            write_report(args.report, consumer_name, provider_name, red_passed, green_passed)
        except OSError as error:
            print(f"無法寫入 TDD 報告 {args.report}: {error}", file=sys.stderr)
            return 1
    return 0 if red_passed and green_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())