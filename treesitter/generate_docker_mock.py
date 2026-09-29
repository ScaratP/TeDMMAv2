from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path


WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_KARATE_DIR = WORKSPACE_ROOT / "treesitter/karate_feature"
DEFAULT_PACT_DIR = WORKSPACE_ROOT / "treesitter/pact_contract"
DEFAULT_FALLBACK_PACT_DIR = WORKSPACE_ROOT / "docker_sandbox_mock/tests/pact"
DEFAULT_OUTPUT = WORKSPACE_ROOT / "docker_sandbox_mock"
DEFAULT_REPORT = WORKSPACE_ROOT / "scratch/phase2_docker_report.json"


def provider_for_feature(feature: Path) -> str:
    stem = re.sub(r"_api_test(?:_\d+)?$", "", feature.stem)
    if stem == "spring-petclinic-main":
        return "pet-service"
    return stem


def normalize_feature(path: Path) -> str:
    text = path.read_text(encoding="utf-8-sig")
    block = re.search(r"```(?:gherkin|karate|feature)\s*\n(.*?)\n```", text, re.DOTALL | re.IGNORECASE)
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


def rewrite_karate_urls(text: str, provider: str) -> str:
    stub_host = f"http://{provider}-stub:8080"
    return re.sub(
        r"https?://(?:localhost|127\.0\.0\.1|[A-Za-z0-9.-]+)(?::\d+)?",
        stub_host,
        text,
        flags=re.IGNORECASE,
    )


def load_pact(path: Path) -> str:
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
    return json.dumps(pact, indent=2, ensure_ascii=False) + "\n"


def interaction_count(path: Path) -> int:
    text = path.read_text(encoding="utf-8-sig")
    lines = text.splitlines()
    while lines and re.match(r"^\s*(//|#)", lines[0]):
        lines.pop(0)
    return len(json.loads("\n".join(lines)).get("interactions", []))


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
RUN echo \"function fn() { return {}; }\" > karate-config.js

COPY karate/ /usr/src/app/
"""


def compose_file(providers: list[str], pact_files: list[str]) -> str:
    lines = ["services:"]
    for provider, pact_file in zip(providers, pact_files):
        service = f"{provider}-stub"
        lines.extend(
            [
                f"  {service}:",
                "    image: pactfoundation/pact-stub-server:latest",
                "    volumes:",
                f"      - ./tests/pact/{pact_file}:/app/pacts/{pact_file}:ro",
                '    command: ["-p", "8080", "-d", "/app/pacts"]',
                "",
            ]
        )

    lines.extend(
        [
            "  karate-runner:",
            "    build:",
            "      context: ./tests",
            "      dockerfile: Dockerfile.karate",
            "    depends_on:",
        ]
    )
    for provider in providers:
        lines.extend(
            [
                f"      {provider}-stub:",
                "        condition: service_started",
            ]
        )
    lines.extend(
        [
            "    volumes:",
            "      - ./test_reports/karate:/usr/src/app/target",
            "    command: >",
            "      bash -c \"",
            "      echo '=== Karate Pact mock tests started ===' > /usr/src/app/target/karate_console.txt &&",
            "      sleep 3 && java -jar karate.jar . >> /usr/src/app/target/karate_console.txt 2>&1",
            "      \"",
            "",
        ]
    )
    return "\n".join(lines)


def write_phase2_report(
    report_path: Path,
    feature_count: int,
    providers: list[str],
    docker_exit_code: int | None,
    console_path: Path,
) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "timestamp": datetime.now().astimezone().isoformat(),
        "karate_features_count": feature_count,
        "providers": providers,
        "docker_exit_code": docker_exit_code,
        "status": "Pass" if docker_exit_code == 0 else "Fail",
        "report_console_path": str(console_path.resolve()),
    }
    report_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def run_docker_tests(
    output: Path,
    feature_count: int,
    providers: list[str],
    report_path: Path,
    auto_down: bool,
) -> int | None:
    compose_file_path = output / "docker-compose.yml"
    console_path = output / "test_reports/karate/karate_console.txt"
    docker_exit_code: int | None = None

    try:
        result = subprocess.run(
            [
                "docker",
                "compose",
                "-f",
                str(compose_file_path),
                "up",
                "--build",
                "--exit-code-from",
                "karate-runner",
            ],
            check=False,
        )
        docker_exit_code = result.returncode
    except (FileNotFoundError, OSError, subprocess.SubprocessError) as error:
        print(f"Docker test execution failed: {error}")

    write_phase2_report(
        report_path,
        feature_count,
        providers,
        docker_exit_code,
        console_path,
    )
    print(f"Phase 2 report: {report_path.resolve()}")

    if auto_down:
        try:
            subprocess.run(
                [
                    "docker",
                    "compose",
                    "-f",
                    str(compose_file_path),
                    "down",
                    "-v",
                ],
                check=False,
            )
        except (FileNotFoundError, OSError, subprocess.SubprocessError) as error:
            print(f"Docker cleanup failed: {error}")

    return docker_exit_code


def generate(
    karate_dir: Path,
    pact_dir: Path,
    fallback_pact_dir: Path,
    output: Path,
    clean: bool,
    run: bool,
    auto_down: bool,
    report_path: Path,
) -> None:
    karate_dir = karate_dir.resolve()
    pact_dir = pact_dir.resolve()
    fallback_pact_dir = fallback_pact_dir.resolve()
    output = output.resolve()

    features = sorted(karate_dir.glob("*.feature"))
    if not features:
        raise FileNotFoundError(f"No .feature files found in {karate_dir}")

    feature_pairs = []
    skipped_features = []
    for feature in features:
        provider = provider_for_feature(feature)
        try:
            pact_source = find_pact(provider, pact_dir, fallback_pact_dir)
        except FileNotFoundError:
            skipped_features.append((feature, provider))
            continue
        feature_pairs.append((feature, provider, pact_source))

    if not feature_pairs:
        providers = sorted({provider_for_feature(feature) for feature in features})
        raise FileNotFoundError(
            "No feature/contract pairs found. "
            f"Features require matching '*_contract*.json' files for providers: {', '.join(providers)}. "
            f"Checked {pact_dir} and {fallback_pact_dir}."
        )

    if skipped_features:
        skipped = ", ".join(feature.name for feature, _ in skipped_features)
        print(f"Skipped features without a matching Pact contract: {skipped}")

    features = [feature for feature, _, _ in feature_pairs]
    feature_providers = [provider for _, provider, _ in feature_pairs]
    providers = list(dict.fromkeys(feature_providers))
    pact_sources = []
    for provider in providers:
        pact_sources.append(
            next(source for _, pair_provider, source in feature_pairs if pair_provider == provider)
        )
    pact_contents = [load_pact(path) for path in pact_sources]

    if output.exists() and clean:
        shutil.rmtree(output)
    elif output.exists() and any(output.iterdir()):
        raise FileExistsError(
            f"Output directory is not empty: {output}. Use --clean to replace it."
        )

    karate_output = output / "tests/karate"
    pact_output = output / "tests/pact"
    report_output = output / "test_reports/karate"
    karate_output.mkdir(parents=True, exist_ok=True)
    pact_output.mkdir(parents=True, exist_ok=True)
    report_output.mkdir(parents=True, exist_ok=True)

    for feature, provider in zip(features, feature_providers):
        content = rewrite_karate_urls(normalize_feature(feature), provider)
        (karate_output / feature.name).write_text(content, encoding="utf-8")

    pact_names = []
    for source, content in zip(pact_sources, pact_contents):
        pact_name = source.name
        pact_names.append(pact_name)
        (pact_output / pact_name).write_text(content, encoding="utf-8")

    (output / "tests/Dockerfile.karate").write_text(dockerfile_karate(), encoding="utf-8")
    (output / "docker-compose.yml").write_text(
        compose_file(providers, pact_names), encoding="utf-8"
    )
    (output / ".gitignore").write_text("test_reports/\n", encoding="utf-8")

    print(f"Generated Docker mock sandbox: {output}")
    print(f"Karate features: {len(features)}")
    print(f"Pact providers: {', '.join(providers)}")

    if run:
        run_docker_tests(
            output,
            len(features),
            providers,
            report_path.resolve(),
            auto_down,
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate a Pact stub-server and Karate-runner Docker mock sandbox."
    )
    parser.add_argument("--karate-dir", type=Path, default=DEFAULT_KARATE_DIR)
    parser.add_argument("--pact-dir", type=Path, default=DEFAULT_PACT_DIR)
    parser.add_argument("--fallback-pact-dir", type=Path, default=DEFAULT_FALLBACK_PACT_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Replace an existing output directory.",
    )
    parser.add_argument(
        "--run",
        action="store_true",
        help="Run the generated Docker Compose Karate tests.",
    )
    parser.add_argument(
        "--auto-down",
        action="store_true",
        help="Run docker compose down -v after the test and report are finished.",
    )
    parser.add_argument(
        "--report-path",
        type=Path,
        default=DEFAULT_REPORT,
        help="Path for the phase 2 Docker report JSON.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    arguments = parse_args()
    generate(
        arguments.karate_dir,
        arguments.pact_dir,
        arguments.fallback_pact_dir,
        arguments.output,
        arguments.clean,
        arguments.run,
        arguments.auto_down,
        arguments.report_path,
    )
