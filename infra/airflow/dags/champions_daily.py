"""챔피언스 사용률 수집 DAG (Phase 2-6, ADR-011 / ADR-012).

    [수집]  --성공하면-->  [만료 정리]

★ 이 파일에 수집 로직이 없다 ★

DAG 는 "언제, 몇 번 시도할지"만 안다. 실제 일은 `pokehelper-backend` 이미지 안의
CLI 가 한다. 그래서 Airflow 를 걷어내도 수집이 돌고(`docker compose exec backend
uv run python -m scripts.run_ingest`), 로직을 고칠 때 Airflow 를 띄울 필요가 없다.

★ 왜 DockerOperator 인가 ★

Airflow 3.4 는 SQLAlchemy 2.0.55 를 고정하고 우리 백엔드는 2.1.8 을 쓴다. 한 환경에
둘을 넣으면 하나는 자기가 요구하지 않은 버전으로 돈다. 그래서 **별도 컨테이너**로
띄운다. 두 환경이 서로를 모른다.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta

from airflow.sdk import DAG
from airflow.providers.docker.operators.docker import DockerOperator

# 우리 백엔드 이미지. compose 가 `pokehelper-backend` 로 빌드한다.
BACKEND_IMAGE = os.environ.get("POKEHELPER_BACKEND_IMAGE", "pokehelper-backend:latest")

# 작업 컨테이너가 붙을 네트워크. 여기 붙어야 `db` 호스트명으로 DB 에 닿는다.
# compose 프로젝트명(디렉터리명)에서 나오는 이름이라 바뀌면 여기도 바꿔야 한다.
NETWORK = os.environ.get("POKEHELPER_NETWORK", "pokehelper_default")

# 작업 컨테이너에 넘길 환경. compose 안이므로 호스트명은 `db` 다.
TASK_ENV = {
    "DATABASE_URL": os.environ.get(
        "POKEHELPER_DATABASE_URL",
        "postgresql+asyncpg://champs:devpassword@db:5432/champs",
    ),
}

INGEST_LIMIT = os.environ.get("POKEHELPER_INGEST_LIMIT", "50")


def backend_task(task_id: str, command: str, **overrides) -> DockerOperator:
    """백엔드 이미지로 한 번 실행하는 작업.

    `auto_remove="success"` — 성공하면 컨테이너를 지우고 **실패하면 남긴다.**
    실패한 컨테이너를 남겨야 `docker logs` 로 들여다볼 수 있다.

    `mount_tmp_dir=False` — Airflow 가 기본으로 호스트 임시 디렉터리를 마운트하는데,
    Windows/Docker Desktop 조합에서 경로 변환 때문에 실패한다. 우리 작업은 임시
    디렉터리를 쓰지 않으므로 끈다.
    """
    return DockerOperator(
        task_id=task_id,
        image=BACKEND_IMAGE,
        command=command,
        docker_url="unix://var/run/docker.sock",
        network_mode=NETWORK,
        environment=TASK_ENV,
        auto_remove="success",
        mount_tmp_dir=False,
        # 이미지를 로컬에서 빌드하므로 레지스트리에서 받지 않는다.
        force_pull=False,
        **overrides,
    )


with DAG(
    dag_id="champions_daily",
    description="챔피언스 사용률 수집 후 만료 스냅샷 정리",
    schedule="0 6 * * *",  # 매일 06:00
    start_date=datetime(2026, 11, 1),
    # ★ 반드시 False.
    # catchup=True 면 start_date 부터 오늘까지의 모든 날짜를 소급 실행한다.
    # 우리는 스냅샷을 14일만 보존하고 과거 사용률을 다시 받을 방법도 없다.
    # 소급 실행은 같은 '현재' 데이터를 날짜만 바꿔 중복 수집하는 짓이고, 출처에
    # 요청만 늘린다 (CLAUDE.md 8-2 요청량 통제).
    catchup=False,
    # 앞 실행이 안 끝났는데 다음 스케줄이 오면 겹쳐 돌지 않게 한다.
    # 수집이 느려졌을 때 두 실행이 같은 스냅샷을 만들려 다투는 걸 막는다.
    max_active_runs=1,
    default_args={
        "retries": 2,
        "retry_delay": timedelta(minutes=10),
        # ★ 재시도가 의미 있는 경우와 아닌 경우
        # 네트워크 끊김(종료 코드 1)은 10분 뒤 되면 성공할 수 있다.
        # 매핑 실패(종료 코드 2)는 사람이 고치기 전엔 몇 번을 해도 같다.
        # 지금은 구분하지 않는다 — 2를 재시도해도 멱등해서 해롭지 않고,
        # 구분하려면 종료 코드를 보고 분기하는 코드가 DAG 에 들어와야 한다.
    },
    tags=["pokehelper", "ingestion"],
) as dag:
    ingest = backend_task(
        task_id="ingest_singles",
        command=f"uv run python -m scripts.run_ingest --format Singles --limit {INGEST_LIMIT}",
        # 요청 간 1초라 50마리면 1분 남짓. 넉넉히 잡되 무한정 매달리지 않게 한다.
        execution_timeout=timedelta(minutes=15),
        doc_md=(
            "상위 N마리 채용률을 수집해 스냅샷으로 적재한다.\n\n"
            "**종료 코드**\n"
            "- `0` 활성 스냅샷 갱신됨\n"
            "- `1` 수집 실패 (네트워크·HTTP)\n"
            "- `2` 적재는 됐지만 **활성화 못 함** — 한영 매핑을 고쳐야 한다\n\n"
            "`2` 로 실패하면 이전 스냅샷이 계속 서비스한다. 데이터 유실은 없다."
        ),
    )

    cleanup = backend_task(
        task_id="cleanup_expired",
        command="uv run python -m scripts.cleanup_snapshots",
        execution_timeout=timedelta(minutes=5),
        doc_md=(
            "보존 기한이 지난 **비활성** 스냅샷을 지운다.\n\n"
            "활성 스냅샷은 만료돼도 남긴다 — 지우면 서비스가 볼 메타가 사라진다."
        ),
    )

    # 수집이 성공해야 정리를 돌린다.
    # 반대로 하면, 수집이 실패한 날 오래된 스냅샷만 지워져 보존 기간이 짧아진다.
    ingest >> cleanup
