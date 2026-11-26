param(
    [string]$Python = "python",
    [string[]]$TestPaths = @('tests/test_postgres_inventory_concurrency.py', 'tests/test_postgres_integration.py', 'tests/test_vision_postgres_http.py', 'tests/test_family_postgres_http.py', 'tests/test_recommendation_read_postgres.py', 'tests/test_recommendation_rules_postgres.py', 'tests/test_recommendation_http_postgres.py', 'tests/test_cooking_postgres_http.py', 'tests/test_cooking_completion_postgres.py', 'tests/test_meal_reporting_postgres.py')
)

$ErrorActionPreference = "Stop"
$containerId = $null
$previousBackend = $env:KINI_DB_BACKEND
$previousUrl = $env:KINI_DATABASE_URL
$previousPassword = $env:POSTGRES_PASSWORD
Push-Location (Split-Path -Parent $PSScriptRoot)
try {
    $env:POSTGRES_PASSWORD = [Guid]::NewGuid().ToString("N")
    $containerId = docker run --rm -d --mount type=tmpfs,destination=/var/lib/postgresql/data -e POSTGRES_PASSWORD -e POSTGRES_DB=kini_test -p 127.0.0.1::5432 postgres:16-alpine
    if ($LASTEXITCODE -ne 0 -or -not $containerId) { throw "Could not start disposable PostgreSQL." }

    $ready = $false
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        docker exec $containerId pg_isready -h 127.0.0.1 -U postgres -d kini_test | Out-Null
        if ($LASTEXITCODE -eq 0) { $ready = $true; break }
        Start-Sleep -Milliseconds 500
    }
    if (-not $ready) { throw "Disposable PostgreSQL did not start." }

    $binding = docker port $containerId 5432/tcp
    if ($LASTEXITCODE -ne 0 -or $binding -notmatch '^127\.0\.0\.1:(\d+)$') {
        throw "Could not locate the loopback PostgreSQL port."
    }
    $port = $Matches[1]
    $env:KINI_DB_BACKEND = "postgres"
    $env:KINI_DATABASE_URL = "postgresql://postgres:${env:POSTGRES_PASSWORD}@127.0.0.1:${port}/kini_test"
    docker exec $containerId postgres --version
    if ($LASTEXITCODE -ne 0) { throw "Could not identify PostgreSQL version." }
    & $Python scripts/run_migrations.py
    if ($LASTEXITCODE -ne 0) { throw "Migration failed." }
    & $Python -m pytest -q --tb=short @TestPaths
    if ($LASTEXITCODE -ne 0) { throw "PostgreSQL tests failed." }
} finally {
    $env:KINI_DB_BACKEND = $previousBackend
    $env:KINI_DATABASE_URL = $previousUrl
    $env:POSTGRES_PASSWORD = $previousPassword
    Pop-Location
    if ($containerId) {
        docker stop $containerId | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Could not stop the disposable PostgreSQL container." }
    }
}
