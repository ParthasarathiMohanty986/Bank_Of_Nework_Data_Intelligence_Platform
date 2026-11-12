pipeline {
    agent { label 'capstone-windows' }

    options {
        skipDefaultCheckout()
        disableConcurrentBuilds()
        timeout(time: 30, unit: 'MINUTES')
        buildDiscarder(logRotator(numToKeepStr: '10', artifactNumToKeepStr: '3'))
    }

    environment {
        REPORT_DIR = "reports/${BUILD_NUMBER}"
        APP_IMAGE = "capstone-settlement:jenkins-${BUILD_NUMBER}"
        TRIVY_IMAGE = 'aquasec/trivy@sha256:8f6bb947dbc528902e11f2f5be52f7b519f6a39d5d148f41166f2040499768fb'
    }

    stages {
        stage('Checkout') {
            steps {
                checkout scm
            }
        }
        stage('Install dependencies') {
            steps {
                powershell '''
                    New-Item -ItemType Directory -Force $env:REPORT_DIR | Out-Null
                    python -m venv .venv
                    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
                    .venv/Scripts/python.exe -m pip install -r requirements-dev.txt bandit==1.9.4 pip-audit==2.10.1
                    exit $LASTEXITCODE
                '''
            }
        }
        stage('Test') {
            steps {
                powershell '''
                    .venv/Scripts/python.exe -m pytest -v --junitxml="$env:REPORT_DIR/pytest.xml"
                    exit $LASTEXITCODE
                '''
            }
        }
        stage('Security checks') {
            steps {
                powershell '''
                    $failed = $false
                    .venv/Scripts/python.exe -m bandit -r settlement -f json -o "$env:REPORT_DIR/bandit.json"
                    if ($LASTEXITCODE -ne 0) { $failed = $true }
                    .venv/Scripts/python.exe -m pip_audit -r requirements.txt -f json -o "$env:REPORT_DIR/dependencies.json"
                    if ($LASTEXITCODE -ne 0) { $failed = $true }

                    $repo = (Get-Location).Path
                    $reports = (Resolve-Path $env:REPORT_DIR).Path
                    docker run --rm --mount "type=bind,source=$repo,target=/src,readonly" --mount "type=bind,source=$reports,target=/out" -e GIT_CONFIG_COUNT=1 -e GIT_CONFIG_KEY_0=safe.directory -e GIT_CONFIG_VALUE_0=/src ghcr.io/gitleaks/gitleaks:v8.30.1 git --redact=100 --no-banner --report-format json --report-path /out/gitleaks.json /src
                    if ($LASTEXITCODE -ne 0 -or $failed) { exit 1 }
                '''
            }
        }
        stage('Build and scan image') {
            steps {
                powershell '''
                    $ErrorActionPreference = 'Stop'
                    docker build --pull --label "org.opencontainers.image.revision=$env:GIT_COMMIT" -t $env:APP_IMAGE .
                    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
                    $imageId = docker image inspect --format '{{.Id}}' $env:APP_IMAGE
                    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
                    $reports = (Resolve-Path $env:REPORT_DIR).Path
                    $archive = Join-Path $reports 'capstone-image.tar'
                    docker save -o $archive $env:APP_IMAGE
                    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
                    docker run --rm --mount "type=bind,source=$archive,target=/image.tar,readonly" --mount "type=bind,source=$reports,target=/out" $env:TRIVY_IMAGE image --input /image.tar --scanners vuln --no-progress --format json --output /out/trivy-image.json --exit-code 0
                    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

                    $scan = Get-Content (Join-Path $reports 'trivy-image.json') -Raw | ConvertFrom-Json
                    if (!$scan.Results) { throw 'Trivy produced no scan results' }
                    $findings = @($scan.Results | ForEach-Object Vulnerabilities | Where-Object { $_ })
                    $critical = @($findings | Where-Object Severity -eq 'CRITICAL').Count
                    [ordered]@{
                        image = $env:APP_IMAGE
                        image_id = $imageId
                        commit = $env:GIT_COMMIT
                        archive_sha256 = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash
                        critical_findings = $critical
                    } | ConvertTo-Json | Set-Content (Join-Path $reports 'image.json')
                    if ($critical -gt 0) { throw "Image has $critical critical findings" }
                '''
            }
        }
        stage('Publish artifact') {
            steps {
                archiveArtifacts artifacts: "${env.REPORT_DIR}/capstone-image.tar", fingerprint: true
            }
        }
    }

    post {
        always {
            junit testResults: "${env.REPORT_DIR}/pytest.xml", allowEmptyResults: true
            archiveArtifacts artifacts: "${env.REPORT_DIR}/*.json,${env.REPORT_DIR}/*.xml", allowEmptyArchive: true
        }
    }
}
