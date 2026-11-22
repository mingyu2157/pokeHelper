############################################################
# 상태 저장소 — RDS PostgreSQL · ElastiCache Redis (ADR-020 결정 2·3)
#
# ★ 이 파일이 **월 청구액의 절반**을 만든다 (RDS $18 + ElastiCache $18).
#   인스턴스 클래스나 Multi-AZ 를 바꾸면 ADR-020 의 비용표가 무효가 된다.
#
# ★ 적용 시점: `ecs.tf` 직전까지 미룬다.
#   RDS 는 **만든 순간부터 시간당 과금**인데 접속할 태스크가 아직 없다. 빈 DB 를
#   켜두고 하루 $1.2 를 내는 것은 아무것도 사지 않는 지출이다.
#   그래서 이 파일은 작성·검증(plan / fmt / validate / 테스트)까지만 하고
#   apply 는 ALB·ECS 와 함께 한다. (2026-11-23 결정)
############################################################

############################################################
# 서브넷 그룹 — 둘 다 프라이빗이다
############################################################

# ★ AZ 두 개를 요구받는다. 단일 AZ 로 운영하기로 했는데도(ADR-020 결정 3)
#   서브넷 그룹에 두 AZ 를 넣는 이유는 AWS 가 그걸 강제하기 때문이다.
#   **서브넷이 두 AZ 에 있는 것과 인스턴스가 두 AZ 에 뜨는 것은 다르다** —
#   아래 `multi_az = false` 가 인스턴스를 한 AZ 에 묶는다.
resource "aws_db_subnet_group" "main" {
  name        = "${local.name}-db"
  description = "Private subnets for RDS. Not routable to the internet."
  subnet_ids  = [for subnet in aws_subnet.private : subnet.id]

  tags = { Name = "${local.name}-db" }
}

resource "aws_elasticache_subnet_group" "main" {
  name        = "${local.name}-cache"
  description = "Private subnets for ElastiCache."
  subnet_ids  = [for subnet in aws_subnet.private : subnet.id]

  tags = { Name = "${local.name}-cache" }
}

############################################################
# 파라미터 그룹 — 느린 쿼리가 보이게 한다
############################################################

# ★ 로그를 켜는 것은 **공짜가 아니다.** CloudWatch 수집이 $0.76/GB 다
#   (ADR-020 결정 5 에서 비용을 줄이는 쪽을 골랐다). 그래서 "전부 기록"이
#   아니라 **느린 것과 스키마 변경만** 남긴다. 양을 줄여야 켜둘 수 있다.
resource "aws_db_parameter_group" "main" {
  name        = "${local.name}-pg16"
  family      = "postgres16"
  description = "Log slow queries, DDL and lock waits. Keeps log volume low."

  # 1초 이상 걸린 쿼리만 남긴다. 0 으로 두면 **모든 쿼리**가 찍혀서
  # CloudWatch 비용이 조용히 올라간다.
  parameter {
    name  = "log_min_duration_statement"
    value = "1000"
  }

  # 스키마 변경은 전부 남긴다. 양이 적고, "언제 뭐가 바뀌었나"에 답할 수 있다.
  # Alembic 마이그레이션이 실제로 무엇을 했는지 확인하는 유일한 기록이다.
  parameter {
    name  = "log_statement"
    value = "ddl"
  }

  # 락 대기는 드물지만 생기면 원인 추적이 어렵다. 켜두는 값이 크다.
  parameter {
    name  = "log_lock_waits"
    value = "1"
  }

  lifecycle {
    create_before_destroy = true
  }
}

############################################################
# RDS PostgreSQL
############################################################

resource "aws_db_instance" "main" {
  identifier = "${local.name}-db"

  engine = "postgres"

  # ★ 패치 버전을 적지 않는다. `"16"` 으로 주면 AWS 가 그 시점의 최신 마이너를
  #   고른다. 패치 버전을 박으면 **그 버전이 없는 리전·시점에서 apply 가 죽고**,
  #   틀린 값을 추측해 넣는 것은 CLAUDE.md 가 금지한다.
  engine_version             = var.db_engine_version
  auto_minor_version_upgrade = true

  instance_class = var.db_instance_class

  # Single-AZ. 가용성보다 비용을 골랐고, 대신 DR 을 백업·복구로 지킨다
  # (ADR-020 결정 3: RPO 5분 / RTO 1시간).
  multi_az = false

  db_name  = var.db_name
  username = var.db_master_username

  # ★★ 비밀번호를 Terraform 이 들고 있지 않는다 ★★
  #
  # `manage_master_user_password = true` 면 RDS 가 비밀번호를 만들어
  # **Secrets Manager 에 직접** 넣는다. Terraform 은 값을 보지 못하므로
  # **state 파일에도, plan 출력에도 평문이 남지 않는다.**
  #
  # 대안이었던 `random_password` + SSM 은 값이 **state 에 평문으로 들어간다.**
  # state 는 S3 에 암호화돼 있지만, "암호화된 곳에 평문이 있다"와 "평문이
  # 없다"는 다르다. README 0장의 "시크릿 값은 Terraform 에 넣지 않는다"를
  # 지키는 유일한 방법이다.
  #
  # 비용: Secrets Manager 시크릿 1개 월 $0.40. 로테이션도 나중에 붙일 수 있다.
  manage_master_user_password = true

  # ★ 로컬과 **같은 사용자명**을 쓴다. 로컬 initdb 는 슈퍼유저로 champs_master
  #   를 만든 뒤 그 계정으로 roles.sql 을 돌리는데(2단계), RDS 에서는 마스터
  #   사용자가 그 champs_master 자리를 그대로 맡는다. 그래서 **같은 roles.sql
  #   이 양쪽에서 동작한다** — RDS 마스터는 슈퍼유저가 아니고, 로컬도 일부러
  #   비슈퍼유저로 맞춰뒀다 (ADR-015, JOURNAL 2026-11-18).

  # 스토리지 — gp3 최소 20GB
  storage_type      = "gp3"
  allocated_storage = var.db_allocated_storage

  # ★ 자동 확장 상한. 디스크가 꽉 차면 DB 가 멈추는데, 그건 알림이 아니라
  #   장애로 알게 된다. 상한을 둬서 **멈추는 대신 늘어나게** 한다. 늘어난
  #   만큼만 과금되므로 평소 비용은 그대로다.
  max_allocated_storage = var.db_max_allocated_storage

  storage_encrypted = true # 기본 KMS 키(aws/rds). 끄는 선택지는 없다

  db_subnet_group_name   = aws_db_subnet_group.main.name
  vpc_security_group_ids = [aws_security_group.rds.id]
  parameter_group_name   = aws_db_parameter_group.main.name

  # ★ 프라이빗 서브넷에 있지만 **이것도 같이 꺼야 한다.** 서브넷이 프라이빗인
  #   것과 RDS 가 퍼블릭 DNS 를 갖는 것은 별개 설정이고, 둘 중 하나만 지키면
  #   의도와 다른 상태가 된다. test_terraform_data.py 가 둘 다 검사한다.
  publicly_accessible = false

  ############################################################
  # DR — RPO 5분 / RTO 1시간 (ADR-020 결정 3)
  ############################################################

  # 자동 백업 7일. 이게 켜져 있으면 **5분 단위 PITR** 이 가능하고, 그게 RPO
  # 5분의 근거다. 스냅샷만으로는 하루 단위밖에 못 되돌린다.
  backup_retention_period = var.db_backup_retention_days

  # UTC 다. 18:00-19:00 UTC = **03:00-04:00 KST** (한국 사용자가 가장 적은 시각)
  backup_window = "18:00-19:00"

  # 백업 창과 겹치면 안 된다. sun:19:30 UTC = **월요일 04:30 KST**
  maintenance_window = "sun:19:30-sun:20:30"

  copy_tags_to_snapshot = true

  # ★ 지우기 전에 한 번 더 막는다. `terraform destroy` 가 **거부된다.**
  #   끌 때는 이 값을 false 로 바꿔 apply 한 뒤 destroy 하는 2단계다.
  #   번거로운 것이 목적이다 — 운영 DB 는 실수로 지워지면 안 된다.
  deletion_protection = true

  # ★ 최종 스냅샷을 남긴다. 그런데 **남은 스냅샷은 계속 과금된다**
  #   (README 6장). destroy 후 콘솔에서 확인하고 직접 지운다.
  #
  #   이름이 고정이라, 같은 이름의 스냅샷이 남아 있으면 **다음 destroy 가
  #   실패한다.** timestamp() 로 매번 다르게 만들 수도 있지만 그러면 plan 에
  #   영원히 변경이 뜬다(매 실행마다 값이 달라지므로). 고정 이름 + 수동 정리
  #   쪽이 조용히 틀리지 않는다.
  skip_final_snapshot       = false
  final_snapshot_identifier = "${local.name}-final"

  # 관측 보조 기능은 끈다 — 비용 때문이다 (ADR-020 결정 5).
  #   - 향상된 모니터링(monitoring_interval): CloudWatch 사용자 지정 지표 과금
  #   - Performance Insights: t4g.micro 지원 여부를 확인하지 않았다.
  #     확인 없이 켜면 apply 가 거기서 죽는다. 필요해지면 그때 확인하고 켠다.
  monitoring_interval          = 0
  performance_insights_enabled = false

  # 파라미터 그룹에서 켠 로그를 CloudWatch 로 보낸다. 느린 쿼리와 DDL 만
  # 찍히므로 양이 적다.
  enabled_cloudwatch_logs_exports = ["postgresql"]

  # ★ 즉시 적용하지 않는다. 설정 변경이 바로 반영되면 **운영 중 재시작**이
  #   일어날 수 있다. 유지보수 창에 모아서 반영한다.
  apply_immediately = false

  tags = { Name = "${local.name}-db", Tier = "private", Backup = "automated-7d" }
}

############################################################
# ElastiCache Redis
############################################################

resource "aws_elasticache_replication_group" "main" {
  replication_group_id = "${local.name}-redis"
  description          = "LLM response cache, ingestion cache and rate limiting."

  engine                     = "redis"
  engine_version             = var.cache_engine_version
  auto_minor_version_upgrade = true
  node_type                  = var.cache_node_type

  # 노드 1개. 자동 페일오버는 복제본이 있어야 가능하므로 같이 끈다.
  #
  # ★ 캐시를 잃는 것이 **데이터를 잃는 것과 다르다**는 전제다. 여기 있는 것은
  #   LLM 응답 캐시와 수집 캐시뿐이고, 날아가면 다시 계산·수집하면 된다
  #   (CLAUDE.md 7장: 동일 입력은 TTL 7일 캐시). 복제본 하나가 월 $18 을
  #   더하는데, 그 돈으로 사는 것이 "다시 계산하지 않아도 되는 것"뿐이다.
  num_cache_clusters         = 1
  automatic_failover_enabled = false
  multi_az_enabled           = false

  parameter_group_name = "default.redis7"
  subnet_group_name    = aws_elasticache_subnet_group.main.name
  security_group_ids   = [aws_security_group.cache.id]

  at_rest_encryption_enabled = true

  # ★★ 앱 코드에 영향이 있다 ★★
  # 전송 암호화를 켜면 클라이언트가 **TLS 로 붙어야 한다.** 접속 URL 이
  # `redis://` 가 아니라 **`rediss://`** 다 (s 가 둘). 이걸 빼먹으면 접속이
  # 조용히 실패하는 게 아니라 연결 자체가 거부된다 — ecs.tf 에서 환경변수를
  # 만들 때 확인한다.
  transit_encryption_enabled = true

  # ★ AUTH 토큰은 **쓰지 않는다.** 계획 단계에서는 넣으려 했는데 빼기로 했다.
  #
  #   이유: 토큰을 쓰면 그 값을 어딘가 둬야 하는데, Terraform 이 만들면 state
  #   에 평문으로 남는다(위 RDS 에서 피한 바로 그 문제다). 그리고 막는 대상이
  #   이미 막혀 있다 — Redis 는 프라이빗 서브넷에 있고 라우트 테이블에
  #   0.0.0.0/0 이 없으며, 보안그룹이 **태스크 보안그룹에서 오는 6379 만**
  #   허용한다. 거기까지 들어온 주체는 이미 앱 자격증명을 가진 상태다.
  #
  #   즉 비용(state 에 평문)이 이득(이미 막힌 경로를 한 번 더 막음)보다 크다.
  #   나중에 필요해지면 ElastiCache **IAM 인증**(Redis 7 이상)으로 가는 것이
  #   맞다 — 그쪽은 저장할 비밀이 아예 없다.

  # UTC. sun:20:30 = 월요일 05:30 KST. RDS 유지보수 창과 겹치지 않게 뒤에 둔다.
  maintenance_window = "sun:20:30-sun:21:30"

  # ★ 스냅샷을 남기지 않는다. 캐시이기 때문이다. 스냅샷은 스토리지 과금이
  #   붙는데, 복구해서 얻는 것이 "다시 계산하지 않아도 되는 것"뿐이다.
  snapshot_retention_limit = 0

  apply_immediately = false

  tags = { Name = "${local.name}-redis", Tier = "private" }
}

############################################################
# 출력
############################################################

output "db_endpoint" {
  description = "RDS 접속 주소(host:port). 비밀은 아니다 — 프라이빗 서브넷에 있고 보안그룹이 막는다."
  value       = aws_db_instance.main.endpoint
}

output "db_master_secret_arn" {
  description = <<-EOT
    RDS 가 만든 마스터 비밀번호 시크릿의 ARN. **값이 아니라 ARN 이다.**
    ECS 실행 역할이 이 ARN 을 읽을 수 있게 해줘야 한다 (iam.tf).

    값을 보려면:
      aws secretsmanager get-secret-value --secret-id <이 ARN> --query SecretString --output text
  EOT
  value       = aws_db_instance.main.master_user_secret[0].secret_arn
}

output "cache_endpoint" {
  description = "Redis 프라이머리 주소. ★ 접속은 rediss:// (TLS) 다."
  value       = aws_elasticache_replication_group.main.primary_endpoint_address
}
