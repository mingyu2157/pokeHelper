# Airflow 이미지 — 공식 이미지에 docker provider 만 더한다.
#
# ★ 우리 백엔드 코드를 여기 넣지 않는다 ★
#
# Airflow 3.4 는 자기 의존성을 수십 개 고정(pin)한다. constraints 파일 기준으로
# **SQLAlchemy==2.0.55** 인데 우리 백엔드는 2.1.8 을 쓴다. 한 환경에 둘을 넣으면
# **둘 중 하나는 자기가 요구하지 않은 버전으로 돌아간다.** 지금은 한 개뿐이지만
# 의존성이 늘수록 충돌도 는다.
#
# 그래서 DAG 는 DockerOperator 로 `pokehelper-backend` 이미지를 **별도 컨테이너로**
# 띄워 실행한다. 두 환경이 서로를 모른다 (ADR-012).
#
# 버전을 고정하는 이유: Airflow 는 마이너 버전 사이에서도 구성 요소가 바뀐다
# (3.0 에서 webserver -> api-server, dag-processor 분리). 떠있는 태그를 쓰면
# 어느 날 갑자기 컨테이너가 안 뜬다.
FROM apache/airflow:3.4.0-python3.12

# DockerOperator 를 제공하는 패키지. 이것 하나만 추가한다.
# 버전은 Airflow 3.4.0 constraints 가 고정한 값이다 (직접 고른 게 아니다).
# constraints 를 함께 넘겨야 이 패키지가 끌고 오는 의존성이 Airflow 조합을 깨지 않는다.
RUN pip install --no-cache-dir \
    "apache-airflow-providers-docker==4.6.1" \
    --constraint "https://raw.githubusercontent.com/apache/airflow/constraints-3.4.0/constraints-3.12.txt"
