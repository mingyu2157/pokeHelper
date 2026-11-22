<#
.SYNOPSIS
    백엔드·프론트엔드 운영 이미지를 ARM 으로 빌드해 ECR 에 올린다 (ADR-020).

.DESCRIPTION
    ★ 왜 스크립트인가 ★

    매 배포마다 반복하는 일인데, 손으로 치면 틀리기 쉬운 지점이 셋이다.

      1. **태그는 git 커밋 SHA 다.** ECR 리포지토리가 `IMMUTABLE` 이라
         `:latest` 를 두 번 밀 수 없다. 그리고 latest 를 덮어쓰면 **지금
         운영에서 도는 이미지가 무엇인지 알 수 없고 롤백 대상도 사라진다**
         (ecr.tf 참조).
      2. **플랫폼은 linux/arm64 다.** Fargate 에서 x86 대비 약 20% 싸다.
         빼먹으면 x86 이미지가 올라가고, ECS 가 태스크를 못 띄울 때까지 모른다.
      3. **작업 트리가 더러우면 SHA 가 이미지를 설명하지 않는다.** "이 커밋의
         이미지"라고 믿고 롤백했는데 다른 코드인 상황이 생긴다.

.PARAMETER AwsProfile
    AWS CLI 프로필. 기본 pokehelper.

.PARAMETER Region
    ECR 리전. 기본 ap-northeast-2.

.PARAMETER Service
    backend / frontend / all. 기본 all.

.PARAMETER AllowDirty
    작업 트리가 더러워도 진행한다. 태그에 `-dirty` 가 붙는다.

.PARAMETER DryRun
    아무것도 실행하지 않고 **무엇을 할지만** 출력한다.

.EXAMPLE
    .\infra\scripts\push-images.ps1 -DryRun
    .\infra\scripts\push-images.ps1
    .\infra\scripts\push-images.ps1 -Service backend
#>
[CmdletBinding()]
param(
    [string]$AwsProfile = "pokehelper",
    [string]$Region = "ap-northeast-2",
    [ValidateSet("all", "backend", "frontend")]
    [string]$Service = "all",
    [switch]$AllowDirty,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

function Write-Step($text) { Write-Host "`n==> $text" -ForegroundColor Cyan }
function Write-Note($text) { Write-Host "    $text" -ForegroundColor DarkGray }
function Die($text) { Write-Host "`n[실패] $text" -ForegroundColor Red; exit 1 }

# ★ PS 5.1 은 네이티브 명령의 stderr 를 리다이렉트하면 각 줄을 ErrorRecord 로
#   감싸고, $ErrorActionPreference='Stop' 이면 그 자리에서 종료한다. docker 가
#   경고 한 줄("daemon is not using the default seccomp profile")만 내도
#   스크립트가 죽는다. 실측으로 겪었다 (JOURNAL 2026-11-22).
#   그래서 **출력을 받아야 하는 네이티브 호출만** 이 함수로 감싼다.
function Invoke-Native {
    param([Parameter(Mandatory)][scriptblock]$Command)
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { & $Command 2>&1 | Out-String } finally { $ErrorActionPreference = $previous }
}

# 저장소 루트로 이동한다. 스크립트를 어디서 부르든 같게 동작해야 한다.
$repo = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Set-Location $repo

############################################################
# 1. 전제 확인 — 여기서 걸러야 빌드 10분을 버리지 않는다
############################################################

Write-Step "전제 확인"

foreach ($tool in @("git", "docker", "aws")) {
    if ($null -eq (Get-Command $tool -ErrorAction SilentlyContinue)) {
        Die "$tool 를 찾을 수 없다. PATH 를 확인하거나 터미널을 다시 열어라."
    }
}
Write-Note "git / docker / aws 있음"

$null = Invoke-Native { docker info }
if ($LASTEXITCODE -ne 0) { Die "Docker 데몬에 붙을 수 없다. Docker Desktop 을 켜라." }
Write-Note "docker 데몬 응답함"

# ★ ARM 빌드가 10분 가까이 걸린다. 자격증명이 만료됐으면 **빌드 전에** 알아야
#   한다. 빌드가 끝난 뒤 push 에서 터지면 그 시간이 통째로 날아간다.
#
# ★ -DryRun 은 AWS 를 **전혀** 건드리지 않는다. "아무것도 안 한다"고 해놓고
#   계정에 요청을 보내면 그건 dry run 이 아니다.
if ($DryRun) {
    $accountId = "<계정ID>"
    Write-Note "(건너뜀) aws sts get-caller-identity --profile $AwsProfile"
}
else {
    $identity = Invoke-Native { aws sts get-caller-identity --profile $AwsProfile --output json }
    if ($LASTEXITCODE -ne 0) {
        Die "AWS 자격증명이 없다. 먼저 실행하라:`n    aws sso login --profile $AwsProfile"
    }
    $accountId = ($identity | ConvertFrom-Json).Account
    Write-Note "AWS 계정 확인됨 (프로필 $AwsProfile)"
}

############################################################
# 2. 태그 — git 커밋 SHA
############################################################

Write-Step "태그 결정"

$sha = (git rev-parse --short=12 HEAD).Trim()
$dirty = [bool](git status --porcelain)

if ($dirty) {
    if (-not $AllowDirty) {
        # ★ 여기서 문자열을 변수에 먼저 받는 이유: `Die @"` 처럼 함수 인자
        #   자리에 here-string 을 바로 쓰면 PowerShell 이 `@` 를 splat 으로
        #   읽어 구문 오류가 난다.
        $message = @"
작업 트리가 더럽다. 커밋하거나 stash 한 뒤 다시 실행하라.

  ★ 태그가 커밋 SHA 인데 코드가 커밋과 다르면, 나중에 "이 커밋의 이미지"라고
    믿고 롤백했을 때 **다른 코드가 뜬다.**

  그래도 밀어야 하면: -AllowDirty  (태그에 -dirty 가 붙는다)
"@
        Die $message
    }
    $sha = "$sha-dirty"
    Write-Host "    [경고] 작업 트리가 더럽다. 태그: $sha" -ForegroundColor Yellow
}

$registry = "$accountId.dkr.ecr.$Region.amazonaws.com"
Write-Note "태그     : $sha"
Write-Note "레지스트리: $registry"

############################################################
# 3. ECR 로그인 — 토큰은 12시간짜리다
############################################################

Write-Step "ECR 로그인"

if ($DryRun) {
    Write-Note "(건너뜀) aws ecr get-login-password | docker login $registry"
}
else {
    $password = aws ecr get-login-password --region $Region --profile $AwsProfile
    if ($LASTEXITCODE -ne 0) { Die "ECR 토큰을 받지 못했다." }

    # ★ PowerShell 의 파이프는 인코딩을 섞을 수 있다. 변수에 받아 넘긴다.
    $password | docker login --username AWS --password-stdin $registry
    if ($LASTEXITCODE -ne 0) { Die "docker login 실패." }
    Write-Note "로그인 성공 (토큰 유효 12시간)"
}

############################################################
# 4. 빌드 + 푸시
############################################################

$targets = @(
    @{ Name = "backend"; Context = "./backend" },
    @{ Name = "frontend"; Context = "./frontend" }
) | Where-Object { $Service -eq "all" -or $_.Name -eq $Service }

$pushed = @()

foreach ($t in $targets) {
    $image = "$registry/pokehelper/$($t.Name):$sha"

    Write-Step "$($t.Name) 빌드 (linux/arm64)"
    Write-Note $image
    Write-Note "에뮬레이션이라 몇 분 걸린다. 백엔드 약 3분, 프론트 약 8분."

    $buildArgs = @(
        "buildx", "build",
        "--platform", "linux/arm64",
        "--target", "prod",
        "-t", $image,
        "--load",
        $t.Context
    )

    if ($DryRun) {
        Write-Note "(건너뜀) docker $($buildArgs -join ' ')"
    }
    else {
        & docker @buildArgs
        if ($LASTEXITCODE -ne 0) { Die "$($t.Name) 빌드 실패." }

        # ★ 빌드가 됐다고 arm64 인 것은 아니다. --platform 이 무시되거나
        #   캐시가 섞이면 x86 이미지가 그대로 나온다. 그러면 ECS 가 태스크를
        #   못 띄울 때까지 모른다 (JOURNAL 2026-11-19 와 같은 이유).
        $arch = docker image inspect $image --format '{{.Architecture}}'
        if ($arch -ne "arm64") { Die "$($t.Name) 이 arm64 가 아니다 (실제: $arch)." }
        Write-Note "아키텍처 확인: $arch"
    }

    Write-Step "$($t.Name) 푸시"
    if ($DryRun) {
        Write-Note "(건너뜀) docker push $image"
    }
    else {
        docker push $image
        if ($LASTEXITCODE -ne 0) {
            $message = @"
$($t.Name) 푸시 실패.

  'tag invalid' 또는 'cannot be overwritten' 이면 **같은 태그가 이미 있다**.
  리포지토리가 IMMUTABLE 이라 덮어쓸 수 없다 (의도한 것이다).
  커밋을 하나 더 쌓거나, 정말 다시 밀어야 하면 콘솔에서 그 태그를 지워라.
"@
            Die $message
        }
    }

    $pushed += $image
}

############################################################
# 5. 결과
############################################################

Write-Step "완료"
foreach ($image in $pushed) { Write-Host "    $image" -ForegroundColor Green }

Write-Host @"

다음: 이 태그를 ECS 태스크 정의가 참조한다 (ecs.tf, 아직 작성 전).
      태그는 커밋 SHA 이므로 **무엇이 떠 있는지 git 에서 되짚을 수 있다.**
"@ -ForegroundColor DarkGray
