"""경보 규칙·대시보드가 참조하는 지표가 실제로 존재하는가 (ADR-016).

★ 이 테스트가 막는 것 ★

PromQL 에서 존재하지 않는 지표는 **오류가 아니라 빈 결과**다. 경보 규칙이
오타난 이름이나 이미 지워진 이름을 참조하면, 규칙은 유효하고 평가되고
**영원히 울리지 않는다.** 대시보드도 똑같이 "데이터 없음"을 조용히 그린다.

`promtool check rules` 도 이걸 잡지 못한다 — 문법은 맞기 때문이다. 지표를
내보내는 쪽(파이썬)과 참조하는 쪽(YAML/JSON)이 다른 언어로 떨어져 있어서,
이름이 어긋나도 아무 도구가 알려주지 않는다. 그 틈을 이 테스트가 메운다.

ADR-013(상수와 게이트), ADR-014(정의와 집행), ADR-015(계정과 설정)와 같은
모양이다 — **선언한 것과 그것을 쓰는 쪽은 별개의 사실이다.**

범위의 한계: 교차검증 대상은 `pokehelper_*` 뿐이다. `up`, `pg_*`, `redis_*`,
`container_*` 는 익스포터가 내보내므로 백엔드 `/metrics` 에 없다. 그쪽은
스택을 띄워 `promtool`·타깃 상태로 확인한다 (docs/RUNBOOK.md 6장).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from app.main import app

pytestmark = pytest.mark.db

# 컨테이너에서는 /opt/observability 에 마운트되고(compose), CI 는 저장소에서
# 직접 돈다. `/app` 아래에 마운트하지 않는다 — 바인드 마운트는 기존 경로를
# 가린다 (JOURNAL 2026-11-17, scripts/ 가 가려진 사고).
_CANDIDATES = (
    Path("/opt/observability"),
    Path(__file__).resolve().parents[3] / "infra" / "observability",
)

OUR_METRIC = re.compile(r"\bpokehelper_[a-z0-9_]+\b")


def observability_dir() -> Path:
    for candidate in _CANDIDATES:
        if candidate.is_dir():
            return candidate
    # 건너뛰지 않는다. 설정 파일을 못 찾으면 교차검증이 통째로 사라지는데,
    # 그건 "통과"가 아니라 "검증하지 않음"이다.
    pytest.fail(f"관측 설정 디렉토리를 찾을 수 없다. 찾은 곳: {[str(c) for c in _CANDIDATES]}")


@pytest.fixture(scope="module")
def exposed_metrics() -> set[str]:
    """`/metrics` 가 실제로 내보내는 우리 지표 이름들."""
    client = TestClient(app)
    body = client.get("/metrics").text
    names: set[str] = set()
    for line in body.splitlines():
        if line.startswith("# TYPE "):
            names.add(line.split()[2])
    # 카운터는 `_total` 로 노출되지만 TYPE 줄은 접미사 없이 나온다.
    # 히스토그램도 `_bucket`/`_sum`/`_count` 파생을 만든다. 참조 쪽이 쓰는
    # 이름까지 포함시킨다.
    derived = set()
    for name in names:
        derived |= {f"{name}_total", f"{name}_bucket", f"{name}_sum", f"{name}_count"}
    return {n for n in names | derived if n.startswith("pokehelper_")}


@pytest.fixture(scope="module")
def rule_files() -> list[Path]:
    files = sorted((observability_dir() / "prometheus" / "rules").glob("*.yml"))
    assert files, "경보 규칙 파일이 없다"
    return files


class TestRuleFilesAreWellFormed:
    """구조 검증 — 규칙이 Prometheus 가 읽을 모양인가."""

    def test_every_rule_has_the_required_fields(self, rule_files: list[Path]) -> None:
        for path in rule_files:
            document = yaml.safe_load(path.read_text(encoding="utf-8"))
            assert document["groups"], f"{path.name}: groups 가 비어 있다"
            for group in document["groups"]:
                assert group.get("name")
                for rule in group["rules"]:
                    assert rule.get("alert"), f"{path.name}: alert 이름이 없다"
                    assert rule.get("expr"), f"{rule['alert']}: expr 이 없다"
                    assert rule.get("labels", {}).get("severity"), (
                        f"{rule['alert']}: severity 라벨이 없다. "
                        "심각도 없는 경보는 받는 사람이 우선순위를 못 정한다"
                    )
                    annotations = rule.get("annotations", {})
                    assert annotations.get("summary"), f"{rule['alert']}: summary 가 없다"
                    assert annotations.get("runbook"), (
                        f"{rule['alert']}: runbook 이 없다. 무엇을 해야 하는지 없는 경보는 소음이다"
                    )

    def test_every_alert_waits_before_firing(self, rule_files: list[Path]) -> None:
        """`for` 없는 경보는 스크랩 한 번의 흔들림에도 울린다."""
        for path in rule_files:
            document = yaml.safe_load(path.read_text(encoding="utf-8"))
            for group in document["groups"]:
                for rule in group["rules"]:
                    assert rule.get("for"), f"{rule['alert']}: for 가 없다"


class TestReferencedMetricsExist:
    """★ 교차검증 — 참조하는 이름이 노출되는 이름과 일치하는가."""

    def test_alert_rules_reference_only_exposed_metrics(
        self, rule_files: list[Path], exposed_metrics: set[str]
    ) -> None:
        missing: dict[str, set[str]] = {}
        for path in rule_files:
            document = yaml.safe_load(path.read_text(encoding="utf-8"))
            for group in document["groups"]:
                for rule in group["rules"]:
                    referenced = set(OUR_METRIC.findall(rule["expr"]))
                    gap = referenced - exposed_metrics
                    if gap:
                        missing[rule["alert"]] = gap
        assert not missing, f"노출되지 않는 지표를 참조한다: {missing}"

    def test_dashboard_references_only_exposed_metrics(self, exposed_metrics: set[str]) -> None:
        dashboards = sorted((observability_dir() / "grafana" / "dashboards").glob("*.json"))
        assert dashboards, "대시보드 파일이 없다"

        missing: dict[str, set[str]] = {}
        for path in dashboards:
            document = json.loads(path.read_text(encoding="utf-8"))
            for panel in document["panels"]:
                for target in panel.get("targets", []):
                    gap = set(OUR_METRIC.findall(target["expr"])) - exposed_metrics
                    if gap:
                        missing[f"{path.name}:{panel['title']}"] = gap
        assert not missing, f"노출되지 않는 지표를 참조한다: {missing}"

    def test_the_check_is_not_vacuous(self, rule_files: list[Path]) -> None:
        """전제 선행 단정 — 참조된 이름이 실제로 **있었다**.

        정규식이 아무것도 못 잡으면 위 두 테스트가 공짜로 통과한다. 비교 대상이
        비어 있지 않았다는 사실을 따로 단정한다 (TEST_STRATEGY 3-9).
        """
        referenced: set[str] = set()
        for path in rule_files:
            document = yaml.safe_load(path.read_text(encoding="utf-8"))
            for group in document["groups"]:
                for rule in group["rules"]:
                    referenced |= set(OUR_METRIC.findall(rule["expr"]))
        assert len(referenced) >= 2, f"경보가 참조하는 우리 지표가 너무 적다: {referenced}"
