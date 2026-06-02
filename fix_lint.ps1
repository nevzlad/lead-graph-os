#Requires -Version 5.1

# Останавливать скрипт при критических ошибках
$ErrorActionPreference = "Stop"

Write-Host "🔍 Проверка окружения..." -ForegroundColor Cyan

# 1. Проверка наличия Ruff
if (-not (Get-Command ruff -ErrorAction SilentlyContinue)) {
    Write-Host "❌ Ошибка: Ruff не установлен. Установите его через 'pip install ruff'." -ForegroundColor Red
    exit 1
}

# 2. Проверка, что это Git-репозиторий
try {
    $null = git rev-parse --is-inside-work-tree 2>$null
} catch {
    Write-Host "❌ Ошибка: Это не Git-репозиторий или Git не установлен." -ForegroundColor Red
    exit 1
}

# 3. Проверка на наличие незакоммиченных изменений
$gitStatus = git status --porcelain
if ($gitStatus) {
    Write-Host "⚠️ Внимание: У вас есть незакоммиченные изменения. Рекомендуется сделать коммит или stash перед запуском." -ForegroundColor Yellow
    $continueSafe = Read-Host "Продолжить? (y/N)"
    if ($continueSafe -notin @('y', 'Y')) {
        Write-Host "Операция отменена." -ForegroundColor Yellow
        exit 0
    }
}

Write-Host "---------------------------------------------------"
Write-Host "🛠️ Шаг 1: Применение БЕЗОПАСНЫХ исправлений (вкл. неиспользуемые переменные F841)..." -ForegroundColor Cyan
ruff check . --fix

Write-Host "🛠️ Шаг 2: Форматирование кода..." -ForegroundColor Cyan
ruff format .

Write-Host "---------------------------------------------------"
Write-Host "👁️ Шаг 3: Анализ 'скрытых' исправлений (требуют --unsafe-fixes)" -ForegroundColor Cyan
Write-Host "Ниже показан diff изменений. Внимательно изучите его!" -ForegroundColor Yellow
Write-Host "---------------------------------------------------"

# Временно отключаем остановку при ошибке, так как ruff --diff возвращает код 1, если есть изменения
$ErrorActionPreference = "Continue"
ruff check . --diff --unsafe-fixes
$ErrorActionPreference = "Stop"

Write-Host "---------------------------------------------------"
$applyUnsafe = Read-Host "✅ Вы просмотрели diff выше. Применить небезопасные исправления (--unsafe-fixes)? (y/N)"

if ($applyUnsafe -in @('y', 'Y')) {
    Write-Host "🛠️ Применение небезопасных исправлений..." -ForegroundColor Cyan
    ruff check . --fix --unsafe-fixes
    Write-Host "✅ Небезопасные исправления применены." -ForegroundColor Green
} else {
    Write-Host "⏭️ Небезопасные исправления пропущены. Будут закоммичены только безопасные правки." -ForegroundColor Yellow
}

Write-Host "---------------------------------------------------"
Write-Host "💾 Шаг 4: Коммит и отправка изменений..." -ForegroundColor Cyan
git add .

if ($applyUnsafe -in @('y', 'Y')) {
    $commitMsg = "chore: fix ruff linting errors, unused variables and apply unsafe fixes"
} else {
    $commitMsg = "chore: fix ruff linting errors and unused variables (safe fixes only)"
}

git commit -m $commitMsg

Write-Host "🚀 Push изменений в удаленный репозиторий..." -ForegroundColor Cyan
git push

Write-Host "---------------------------------------------------"
Write-Host "🔍 Шаг 5: Финальная проверка (имитация CI)..." -ForegroundColor Cyan

$ErrorActionPreference = "Continue"
ruff check .
$ruffExitCode = $LASTEXITCODE
$ErrorActionPreference = "Stop"

if ($ruffExitCode -eq 0) {
    Write-Host "✅ Отлично! Ошибок линтера не найдено. CI pipeline должен пройти успешно." -ForegroundColor Green
} else {
    Write-Host "❌ Внимание: Остались ошибки линтера. Проверьте вывод выше." -ForegroundColor Red
    Write-Host "💡 Подсказка: Если ruff жалуется на 'niche' переменную, замените её имя на '_' (например: _ = some_function())." -ForegroundColor Yellow
    exit 1
}

Write-Host "🎉 Процесс завершен успешно!" -ForegroundColor Green