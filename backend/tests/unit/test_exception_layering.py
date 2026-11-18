"""예외 계층 규칙을 코드가 지키는지 본다 (CLAUDE.md 6장).

★ 이 테스트가 생긴 이유 ★

CLAUDE.md 는 "예외는 `app/core/exceptions.py` 에 정의한다"고 적어뒀는데,
`core/exceptions.py` 에는 예외 정의가 **하나도 없었다** — 거기는 도메인 예외를
HTTP 로 옮기는 핸들러 등록 모듈이다. 그리고 도메인 예외는 `domain/exceptions.py`
에 있다. 그렇게 둘 수밖에 없다 — CLAUDE.md 1-5 가 "도메인은 아무것도 import
하지 않는다"고 못 박기 때문에, 도메인 예외를 `core/` 에 두면 도메인이 core 를
import 하게 된다. **규칙끼리 모순이었고, 코드가 옳았다.**

그 사이에 도메인 밖 예외 5개가 맨 `Exception` 을 상속한 채 쌓였다. 문서를
고치는 것만으로는 같은 일이 또 생긴다 — **문서는 읽는 사람에게만 작동하고,
테스트는 읽지 않는 사람에게도 작동한다.** (ADR-013·ADR-015·ADR-016 과 같은
모양이다: 선언한 것과 집행되는 것은 별개의 사실이다.)

규칙:
- 계층마다 베이스 예외를 하나 둔다
  (`DomainError` / `IngestionError` / `ServiceError` / `ConfigError`)
- 그 밖의 모든 예외는 그중 하나를 상속한다
- 베이스들만 맨 `Exception` 을 상속한다
"""

from __future__ import annotations

import importlib
import pkgutil
from pathlib import Path

import pytest

import app as app_package
from app.core.exceptions import ConfigError
from app.domain.exceptions import DomainError
from app.ingestion.exceptions import IngestionError
from app.services.exceptions import ServiceError

#: 계층 베이스. 이 셋만 맨 `Exception` 을 상속해도 된다.
LAYER_BASES: tuple[type[Exception], ...] = (
    DomainError,
    IngestionError,
    ServiceError,
    ConfigError,
)

#: 베이스가 사는 모듈. 각 계층이 예외를 **자급**한다는 규칙의 실체다.
BASE_MODULES = {
    "app.domain.exceptions": DomainError,
    "app.ingestion.exceptions": IngestionError,
    "app.services.exceptions": ServiceError,
    "app.core.exceptions": ConfigError,
}


def _all_app_modules() -> list[str]:
    """`app` 패키지 전체를 import 해 모듈 이름을 모은다.

    파일을 문자열로 훑지 않고 **실제로 import 한다.** 문자열 검색은
    `class Foo(Bar)` 의 `Bar` 가 무엇을 상속하는지 모르기 때문에,
    베이스를 한 다리 건너 상속한 경우를 판정할 수 없다.
    """
    names = []
    root = Path(app_package.__file__).parent
    for info in pkgutil.walk_packages([str(root)], prefix="app."):
        if info.ispkg:
            continue
        names.append(info.name)
    return sorted(names)


@pytest.fixture(scope="module")
def declared_exceptions() -> list[tuple[str, type[BaseException]]]:
    """`app` 안에서 **정의된** 예외 클래스 전부. import 해 온 것은 세지 않는다."""
    found: list[tuple[str, type[BaseException]]] = []
    for name in _all_app_modules():
        module = importlib.import_module(name)
        for attr in vars(module).values():
            if not isinstance(attr, type) or not issubclass(attr, BaseException):
                continue
            # 재노출(`from ... import X`)은 정의가 아니다. 정의한 모듈에서만 센다.
            if attr.__module__ != name:
                continue
            found.append((name, attr))
    return found


class TestLayerBasesExist:
    """전제 선행 단정 — 베이스가 제자리에 있고 서로 독립인가.

    이게 깨지면 아래 테스트들이 "검사할 대상이 없어서" 통과한다 (3-9).
    """

    @pytest.mark.parametrize(("module_name", "base"), list(BASE_MODULES.items()))
    def test_base_lives_in_its_layer(self, module_name: str, base: type[Exception]) -> None:
        module = importlib.import_module(module_name)
        assert getattr(module, base.__name__, None) is base

    def test_bases_are_independent(self) -> None:
        """베이스끼리 상속하지 않는다. 섞이면 계층 구분이 사라진다."""
        for base in LAYER_BASES:
            others = [other for other in LAYER_BASES if other is not base]
            assert not any(issubclass(base, other) for other in others), base


class TestEveryExceptionHasALayer:
    """★ 맨 `Exception` 금지 (CLAUDE.md 6장) 를 실제로 집행한다."""

    def test_no_exception_inherits_bare_exception(
        self, declared_exceptions: list[tuple[str, type[BaseException]]]
    ) -> None:
        offenders = [
            f"{module}.{exc.__name__}"
            for module, exc in declared_exceptions
            if exc not in LAYER_BASES and not issubclass(exc, LAYER_BASES)
        ]
        assert not offenders, (
            "계층 베이스를 상속하지 않는 예외가 있다: "
            f"{offenders}. DomainError / IngestionError / ServiceError 중 하나를 "
            "상속해야 한다 (CLAUDE.md 6장)."
        )

    def test_only_the_bases_inherit_bare_exception(
        self, declared_exceptions: list[tuple[str, type[BaseException]]]
    ) -> None:
        """베이스 셋만 `Exception` 을 직접 상속한다."""
        direct = [
            f"{module}.{exc.__name__}"
            for module, exc in declared_exceptions
            if Exception in exc.__bases__ and exc not in LAYER_BASES
        ]
        assert not direct, f"맨 Exception 을 직접 상속한다: {direct}"

    def test_the_check_is_not_vacuous(
        self, declared_exceptions: list[tuple[str, type[BaseException]]]
    ) -> None:
        """전제 선행 단정 — 검사 대상이 실제로 모였다.

        import 가 실패하거나 탐색이 비면 위 두 단정이 공짜로 통과한다.
        """
        names = {exc.__name__ for _, exc in declared_exceptions}
        assert len(names) >= 20, f"수집된 예외가 {len(names)}개뿐이다: {sorted(names)}"
        for base in LAYER_BASES:
            assert base.__name__ in names, f"{base.__name__} 을 찾지 못했다"


class TestDomainStaysIndependent:
    """도메인 예외는 도메인 안에서만 정의된다 (CLAUDE.md 1-5).

    도메인이 바깥 계층의 예외를 상속하면 그 순간 외부 의존이 생긴다.
    """

    def test_domain_exceptions_do_not_inherit_other_layers(
        self, declared_exceptions: list[tuple[str, type[BaseException]]]
    ) -> None:
        leaks = [
            f"{module}.{exc.__name__}"
            for module, exc in declared_exceptions
            if module.startswith("app.domain.")
            and (issubclass(exc, IngestionError) or issubclass(exc, ServiceError))
        ]
        assert not leaks, f"도메인 예외가 바깥 계층을 상속한다: {leaks}"

    def test_domain_exceptions_live_in_the_domain_package(
        self, declared_exceptions: list[tuple[str, type[BaseException]]]
    ) -> None:
        misplaced = [
            f"{module}.{exc.__name__}"
            for module, exc in declared_exceptions
            if issubclass(exc, DomainError) and not module.startswith("app.domain.")
        ]
        assert not misplaced, f"도메인 예외가 도메인 밖에 있다: {misplaced}"
