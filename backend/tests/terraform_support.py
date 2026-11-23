"""`.tf` 소스를 테스트에서 읽기 위한 공용 유틸.

★ 왜 모듈로 뺐나 ★

컨테이너와 CI 의 **경로가 다르다**. compose 는 저장소를 `/opt/terraform` 에
마운트하고, CI 는 러너의 체크아웃 디렉터리에서 돈다. 어느 한쪽을 기본값으로
박으면 **다른 쪽에서만** 깨진다 — 실제로 그렇게 깨뜨린 적이 있다
(JOURNAL 2026-11-19, 같은 실수 세 번째).

그래서 "후보 경로로 찾는다"를 세 파일이 각자 들고 있었다. 네 번째를 만들지
않으려고 여기로 모았다.

★ 범위의 한계: **`.tf` 소스를 읽는다. 실제 AWS 계정을 보지 않는다.**
`terraform plan` 은 자격증명이 필요해 CI 에서 돌릴 수 없기 때문이다. 따라서
**콘솔에서 손으로 고친 것은 다음 `apply` 전까지 잡히지 않는다.**
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import hcl2
import pytest

# 컨테이너에서는 /opt/terraform 에 마운트되고(compose), CI 는 저장소에서 직접
# 돈다. `/app` 아래에 마운트하지 않는다 — 바인드 마운트는 기존 경로를 가린다
# (JOURNAL 2026-11-17).
_CANDIDATES = (
    Path("/opt/terraform"),
    Path(__file__).resolve().parents[2] / "infra" / "terraform",
)


def terraform_dir() -> Path:
    for candidate in _CANDIDATES:
        if candidate.is_dir():
            return candidate
    # 건너뛰지 않는다. 디렉터리를 못 찾으면 검사가 통째로 사라지는데, 그건
    # "통과"가 아니라 "검증하지 않음"이다.
    pytest.fail(f"Terraform 디렉터리를 찾을 수 없다. 찾은 곳: {[str(c) for c in _CANDIDATES]}")


def unwrap(value: Any) -> Any:
    """python-hcl2 가 블록을 리스트로 감싸는 것을 벗긴다."""
    if isinstance(value, list) and len(value) == 1:
        return value[0]
    return value


def unquote(value: Any) -> Any:
    """★ python-hcl2 7.x 는 블록 라벨과 문자열 값에 따옴표를 **남긴다**.

    `aws_subnet` 이 아니라 `"aws_subnet"` 으로, `0.0.0.0/0` 이 아니라
    `"0.0.0.0/0"` 으로 나온다. 버전에 따라 다르므로, 따옴표가 없어도 그대로
    통과하도록(멱등) 만든다. 여기서 정규화하지 않으면 **모든 비교가 조용히
    빗나가고** 검사가 통째로 공회전한다 — 실제로 그렇게 됐고, 전제 선행
    단정이 잡았다 (JOURNAL 2026-11-19).
    """
    if isinstance(value, str) and len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        return value[1:-1]
    return value


def clean(body: Any) -> Any:
    """파서가 끼워 넣은 메타 키(`__is_block__`·`__comments__`)를 걷어내고
    문자열 값의 따옴표를 벗긴다."""
    body = unwrap(body)
    if isinstance(body, dict):
        return {k: clean(v) for k, v in body.items() if not k.startswith("__")}
    if isinstance(body, list):
        return [clean(v) for v in body]
    return unquote(body)


def load_documents() -> list[dict[str, Any]]:
    """`bootstrap/` 을 뺀 모든 `.tf` 를 파싱한다.

    bootstrap 은 state 저장소(S3·DynamoDB)만 만들고 본체 자원을 건드리지 않는다.
    """
    root = terraform_dir()
    files = sorted(root.glob("*.tf"))
    assert files, f"{root} 에 .tf 파일이 없다"

    parsed = []
    for path in files:
        with path.open(encoding="utf-8") as handle:
            parsed.append(hcl2.load(handle))
    return parsed


def resources(documents: list[dict[str, Any]], kind: str) -> dict[str, dict[str, Any]]:
    """`kind` 타입 리소스를 {이름: 속성} 으로 모은다."""
    found: dict[str, dict[str, Any]] = {}
    for document in documents:
        for block in document.get("resource", []):
            for resource_type, bodies in block.items():
                if unquote(resource_type) != kind:
                    continue
                for name, body in bodies.items():
                    found[unquote(name)] = clean(body)
    return found


def data_sources(documents: list[dict[str, Any]], kind: str) -> dict[str, dict[str, Any]]:
    """`kind` 타입 data 소스를 {이름: 속성} 으로 모은다.

    ★ 왜 필요한가: IAM 정책을 `jsonencode({...})` 로 쓰면 hcl2 가 그 전체를
    **문자열 하나**로 돌려준다. 액션·자원·조건을 따로 볼 수 없어서 "PassRole 의
    Resource 가 `*` 인가" 같은 것을 단정할 수 없다. `aws_iam_policy_document`
    data 소스로 쓰면 `statement` 블록이 HCL 그대로 파싱된다.
    그래서 `iam.tf` 는 정책을 data 소스로 쓴다 — **테스트가 읽을 수 있는 형태로
    쓰는 것**이 조건이었다.
    """
    found: dict[str, dict[str, Any]] = {}
    for document in documents:
        for block in document.get("data", []):
            for data_type, bodies in block.items():
                if unquote(data_type) != kind:
                    continue
                for name, body in bodies.items():
                    found[unquote(name)] = clean(body)
    return found


def blocks(body: dict[str, Any], name: str) -> list[dict[str, Any]]:
    """리소스 안의 반복 블록(`notification` 등)을 항상 리스트로 돌려준다.

    hcl2 는 블록이 하나면 딕셔너리, 여럿이면 리스트로 준다. 그 차이를 호출부가
    매번 다루면 **하나짜리를 세는 코드가 조용히 틀린다.**
    """
    value = body.get(name, [])
    return value if isinstance(value, list) else [value]
