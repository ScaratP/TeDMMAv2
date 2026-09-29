from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path


WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_KARATE_DIR = WORKSPACE_ROOT / "treesitter/karate_feature"
DEFAULT_PACT_DIR = WORKSPACE_ROOT / "treesitter/pact_contract"
DEFAULT_FALLBACK_PACT_DIR = WORKSPACE_ROOT / "docker_sandbox_mock/tests/pact"
DEFAULT_OUTPUT = WORKSPACE_ROOT / "docker_sandbox_contract"
DEFAULT_PROVIDER_URL_TEMPLATE = "http://host.docker.internal:8080"


def provider_for_feature(feature: Path) -> str:
    stem = re.sub(r"_api_test(?:_\d+)?$", "", feature.stem)
    if stem == "spring-petclinic-main":
        return "pet-service"
    return stem


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
        raise ValueError(f"Karate file does not contain a Feature declaration: {path}")
    return text.rstrip() + "\n"


def rewrite_karate_url(text: str, provider: str) -> str:
    provider_expression = f"providerUrls['{provider}']"
    rewritten = re.sub(
        r"(?m)^(\s*\*\s+url\s+)(['\"])[^'\"]+\2",
        lambda match: match.group(1) + provider_expression,
        text,
    )
    if rewritten == text:
        raise ValueError(f"Could not find a Karate url to rewrite for provider: {provider}")
    return rewritten


def load_pact(path: Path) -> tuple[str, dict]:
    text = path.read_text(encoding="utf-8-sig")
    lines = text.splitlines()
    while lines and re.match(r"^\s*(//|#)", lines[0]):
        lines.pop(0)
    try:
        pact = json.loads("\n".join(lines))
    except json.JSONDecodeError as error:
        raise ValueError(f"Invalid Pact JSON: {path}: {error}") from error
    if not pact.get("provider", {}).get("name"):
        raise ValueError(f"Pact has no provider name: {path}")
    return json.dumps(pact, indent=2, ensure_ascii=False) + "\n", pact


def interaction_count(path: Path) -> int:
    _, pact = load_pact(path)
    return len(pact.get("interactions", []))


def find_pact(provider: str, pact_dir: Path, fallback_dir: Path) -> Path:
    candidates = sorted(pact_dir.glob(f"{provider}_contract*.json"))
    candidates.extend(sorted(fallback_dir.glob(f"{provider}_contract*.json")))
    if not candidates:
        raise FileNotFoundError(
            f"No Pact contract found for provider '{provider}'. "
            f"Checked {pact_dir} and {fallback_dir}."
        )
    return max(candidates, key=interaction_count)


def dockerfile_karate() -> str:
    return """FROM maven:3.9.6-eclipse-temurin-21-jammy
WORKDIR /usr/src/app

RUN wget https://github.com/karatelabs/karate/releases/download/v1.4.1/karate-1.4.1.jar -O karate.jar
COPY karate/ /usr/src/app/
COPY karate-config.js /usr/src/app/karate-config.js
"""


def dockerfile_lint() -> str:
    return """FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir jsonschema
COPY pact_lint.py pact_schema.json /app/
"""


def karate_config(provider_urls: dict[str, str]) -> str:
    defaults = json.dumps(provider_urls, ensure_ascii=False)
    return f"""function fn() {{
  var providerUrls = {defaults};
  var configuredUrls = java.lang.System.getenv('PACT_PROVIDER_URLS');
  if (configuredUrls) {{
    providerUrls = JSON.parse(configuredUrls);
  }}
  return {{ providerUrls: providerUrls }};
}}
"""


def pact_schema() -> str:
    return json.dumps(
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "required": ["consumer", "provider", "metadata", "interactions"],
            "properties": {
                "consumer": {"type": "object", "required": ["name"]},
                "provider": {"type": "object", "required": ["name"]},
                "metadata": {
                    "type": "object",
                    "required": ["pactSpecification"],
                    "properties": {
                        "pactSpecification": {
                            "type": "object",
                            "required": ["version"],
                            "properties": {"version": {"const": "3.0.0"}},
                        }
                    },
                },
                "interactions": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "required": ["description", "request", "response", "providerStates"],
                        "properties": {
                            "request": {
                                "type": "object",
                                "required": ["method", "path"],
                            },
                            "response": {
                                "type": "object",
                                "required": ["status"],
                            },
                            "providerStates": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "required": ["name"],
                                },
                            },
                        },
                    },
                },
            },
        },
        indent=2,
    ) + "\n"


def pact_lint() -> str:
    return '''import json
import sys
from pathlib import Path
from jsonschema import Draft202012Validator


def main() -> int:
    schema = json.loads(Path("/app/pact_schema.json").read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    failures = []
    for pact_path in sorted(Path(sys.argv[1]).glob("*.json")):
        try:
            pact = json.loads(pact_path.read_text(encoding="utf-8"))
            errors = sorted(validator.iter_errors(pact), key=lambda error: list(error.path))
            for error in errors:
                location = ".".join(str(item) for item in error.path) or "$"
                failures.append(f"{pact_path.name}: {location}: {error.message}")
        except json.JSONDecodeError as error:
            failures.append(f"{pact_path.name}: invalid JSON: {error}")

    if failures:
        print("Pact lint failed:")
        print("\\n".join(failures))
        return 1
    print("Pact lint passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''


def compose_file(providers: list[str], pact_files: list[str], provider_urls: dict[str, str]) -> str:
    lines = ["services:"]
    for provider, pact_file in zip(providers, pact_files):
        verifier = f"{provider}-pact-verifier"
        lines.extend(
            [
                f"  {verifier}:",
                "    image: pactfoundation/pact-cli:latest",
                "    volumes:",
                "      - ./tests/pact:/app/pact:ro",
                "      - ./test_reports/pact:/app/reports",
                '    entrypoint: ["/bin/sh", "-c"]',
                "    command: >",
                f'      "pact verify --provider-base-url {provider_urls[provider]} '
                f'--pact-urls /app/pact/{pact_file} > /app/reports/{provider}_verification.txt 2>&1"',
                "",
            ]
        )

    lines.extend(
        [
            "  pact-lint:",
            "    build:",
            "      context: ./tests",
            "      dockerfile: Dockerfile.pact-lint",
            "    volumes:",
            "      - ./tests/pact:/pact:ro",
            "      - ./test_reports/pact:/reports",
            "    command: >",
            '      sh -c "python /app/pact_lint.py /pact > /reports/lint.txt 2>&1"',
            "",
            "  karate-runner:",
            "    build:",
            "      context: ./tests",
            "      dockerfile: Dockerfile.karate",
            "    environment:",
            f"      PACT_PROVIDER_URLS: '{json.dumps(provider_urls)}'",
            "    volumes:",
            "      - ./test_reports/karate:/usr/src/app/target",
            "    command: >",
            '      bash -c "java -jar karate.jar . > /usr/src/app/target/karate_console.txt 2>&1"',
            "",
        ]
    )
    return "\n".join(lines)


def generate(
    karate_dir: Path,
    pact_dir: Path,
    fallback_pact_dir: Path,
    output: Path,
    provider_url_template: str,
    clean: bool,
) -> None:
    karate_dir = karate_dir.resolve()
    pact_dir = pact_dir.resolve()
    fallback_pact_dir = fallback_pact_dir.resolve()
    output = output.resolve()
    features = sorted(karate_dir.glob("*.feature"))
    if not features:
        raise FileNotFoundError(f"No .feature files found in {karate_dir}")

    feature_providers = [provider_for_feature(feature) for feature in features]
    providers = list(dict.fromkeys(feature_providers))
    pact_sources = [find_pact(provider, pact_dir, fallback_pact_dir) for provider in providers]
    pact_contents = [load_pact(path)[0] for path in pact_sources]
    provider_urls = {
        provider: provider_url_template.format(provider=provider)
        for provider in providers
    }

    if output.exists() and clean:
        shutil.rmtree(output)
    elif output.exists() and any(output.iterdir()):
        raise FileExistsError(
            f"Output directory is not empty: {output}. Use --clean to replace it."
        )

    karate_output = output / "tests/karate"
    pact_output = output / "tests/pact"
    report_output = output / "test_reports"
    karate_output.mkdir(parents=True, exist_ok=True)
    pact_output.mkdir(parents=True, exist_ok=True)
    (report_output / "karate").mkdir(parents=True, exist_ok=True)
    (report_output / "pact").mkdir(parents=True, exist_ok=True)

    for feature, provider in zip(features, feature_providers):
        content = rewrite_karate_url(normalize_feature(feature), provider)
        (karate_output / feature.name).write_text(content, encoding="utf-8")

    pact_names = []
    for source, content in zip(pact_sources, pact_contents):
        pact_names.append(source.name)
        (pact_output / source.name).write_text(content, encoding="utf-8")

    tests_dir = output / "tests"
    (tests_dir / "Dockerfile.karate").write_text(dockerfile_karate(), encoding="utf-8")
    (tests_dir / "Dockerfile.pact-lint").write_text(dockerfile_lint(), encoding="utf-8")
    (tests_dir / "karate-config.js").write_text(
        karate_config(provider_urls), encoding="utf-8"
    )
    (tests_dir / "pact_lint.py").write_text(pact_lint(), encoding="utf-8")
    (tests_dir / "pact_schema.json").write_text(pact_schema(), encoding="utf-8")
    (output / "docker-compose.yml").write_text(
        compose_file(providers, pact_names, provider_urls), encoding="utf-8"
    )
    (output / ".gitignore").write_text("test_reports/\n", encoding="utf-8")

    print(f"Generated Docker contract sandbox: {output}")
    print(f"Karate features: {len(features)}")
    print(f"Pact providers: {', '.join(providers)}")
    print("Provider URL template: " + provider_url_template)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate Karate, Pact provider verification, and Pact lint services."
    )
    parser.add_argument("--karate-dir", type=Path, default=DEFAULT_KARATE_DIR)
    parser.add_argument("--pact-dir", type=Path, default=DEFAULT_PACT_DIR)
    parser.add_argument("--fallback-pact-dir", type=Path, default=DEFAULT_FALLBACK_PACT_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--provider-url-template",
        default=DEFAULT_PROVIDER_URL_TEMPLATE,
        help="Provider URL; use {provider} for per-provider URLs.",
    )
    parser.add_argument("--clean", action="store_true", help="Replace an existing output directory.")
    return parser.parse_args()


if __name__ == "__main__":
    arguments = parse_args()
    generate(
        arguments.karate_dir,
        arguments.pact_dir,
        arguments.fallback_pact_dir,
        arguments.output,
        arguments.provider_url_template,
        arguments.clean,
    )