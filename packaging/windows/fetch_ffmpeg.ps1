<#
.SYNOPSIS
  Скачивает статическую LGPL-сборку FFmpeg (BtbN/FFmpeg-Builds, win64) и кладёт
  ffmpeg.exe в vendor/ffmpeg/ (его ждёт packaging/build_app.py).

.DESCRIPTION
  * Берёт только bin/ffmpeg.exe и LICENSE.txt (-> LICENSE.ffmpeg.txt) из архива.
  * Выбирает не-shared lgpl-ассет ветки -Branch из checksums.sha256 релиза "latest"
    и сверяет SHA-256 скачанного архива.
  * Идемпотентен: если vendor/ffmpeg/ffmpeg.exe уже получен из того же архива
    (по SHA-256 в .source), ничего не скачивает. -Force перекачивает.
  * Любая ошибка завершает скрипт с ненулевым кодом.

.PARAMETER Branch
  Ветка FFmpeg в именах ассетов BtbN. Ветки 7.1 в релизе "latest" больше нет
  (сейчас n8.1 и n9.0), по умолчанию - стабильная n8.1.

.PARAMETER Tag
  Тег релиза BtbN. "latest" - скользящий; для воспроизводимости можно указать
  датированный (autobuild-YYYY-MM-DD-HH-MM).

.PARAMETER OutDir
  Куда положить результат (по умолчанию <корень репозитория>/vendor/ffmpeg).
#>
[CmdletBinding()]
param(
    [string]$Branch = 'n8.1',
    [string]$Tag = 'latest',
    [string]$OutDir = '',
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$ProgressPreference = 'SilentlyContinue'   # без прогресс-бара Invoke-WebRequest на порядок быстрее
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
if (-not $OutDir) { $OutDir = Join-Path $repoRoot 'vendor\ffmpeg' }
$baseUrl = "https://github.com/BtbN/FFmpeg-Builds/releases/download/$Tag"

function Invoke-Download([string]$Url, [string]$Dest) {
    for ($attempt = 1; $attempt -le 4; $attempt++) {
        try {
            Invoke-WebRequest -Uri $Url -OutFile $Dest -UseBasicParsing
            return
        } catch {
            if ($attempt -eq 4) { throw "Не удалось скачать ${Url}: $($_.Exception.Message)" }
            Write-Warning "Попытка $attempt не удалась ($($_.Exception.Message)), повтор через $($attempt * 5) с"
            Start-Sleep -Seconds ($attempt * 5)
        }
    }
}

$work = Join-Path ([IO.Path]::GetTempPath()) ("bp-ffmpeg-" + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $work | Out-Null
try {
    # 1. Какой именно ассет брать - по checksums.sha256 (не зависит от лимитов API GitHub).
    $sumsFile = Join-Path $work 'checksums.sha256'
    Invoke-Download "$baseUrl/checksums.sha256" $sumsFile
    $pattern = '^(?<sha>[0-9a-fA-F]{64})\s+\*?(?<name>ffmpeg-' + [regex]::Escape($Branch) + '-latest-win64-lgpl-[0-9][0-9.]*\.zip)\s*$'
    $found = @()
    foreach ($line in Get-Content $sumsFile) {
        if ($line -match $pattern) {
            $found += [pscustomobject]@{ Sha = $Matches['sha'].ToLower(); Name = $Matches['name'] }
        }
    }
    if ($found.Count -ne 1) {
        $available = (Get-Content $sumsFile | Where-Object { $_ -match 'win64-lgpl-' -and $_ -notmatch 'shared' } |
            ForEach-Object { ($_ -split '\s+')[-1] }) -join ', '
        throw "Ожидался ровно один статический lgpl-ассет для ветки '$Branch', найдено $($found.Count). Доступные: $available"
    }
    $asset = $found[0]
    Write-Host "Ассет: $($asset.Name) (sha256 $($asset.Sha))"

    # 2. Идемпотентность.
    $exe = Join-Path $OutDir 'ffmpeg.exe'
    $licence = Join-Path $OutDir 'LICENSE.ffmpeg.txt'
    $stamp = Join-Path $OutDir '.source'
    if (-not $Force -and (Test-Path $exe) -and (Test-Path $licence) -and (Test-Path $stamp) -and
        ((Get-Content $stamp -Raw).Trim() -eq $asset.Sha)) {
        Write-Host "vendor/ffmpeg уже актуален: $exe"
        & $exe -hide_banner -version | Select-Object -First 1
        exit 0
    }

    # 3. Скачать и проверить.
    $zip = Join-Path $work $asset.Name
    Write-Host "Скачиваю $baseUrl/$($asset.Name) ..."
    Invoke-Download "$baseUrl/$($asset.Name)" $zip
    $actual = (Get-FileHash -Algorithm SHA256 -Path $zip).Hash.ToLower()
    if ($actual -ne $asset.Sha) { throw "SHA-256 не совпал: ожидался $($asset.Sha), получен $actual" }

    # 4. Достать только ffmpeg.exe и LICENSE.txt.
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $archive = [IO.Compression.ZipFile]::OpenRead($zip)
    try {
        $entryExe = $archive.Entries | Where-Object { $_.FullName -match '^[^/]+/bin/ffmpeg\.exe$' } | Select-Object -First 1
        $entryLic = $archive.Entries | Where-Object { $_.FullName -match '^[^/]+/LICENSE\.txt$' } | Select-Object -First 1
        if (-not $entryExe) { throw 'В архиве нет bin/ffmpeg.exe' }
        if (-not $entryLic) { throw 'В архиве нет LICENSE.txt' }
        New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
        [IO.Compression.ZipFileExtensions]::ExtractToFile($entryExe, $exe, $true)
        [IO.Compression.ZipFileExtensions]::ExtractToFile($entryLic, $licence, $true)
    } finally {
        $archive.Dispose()
    }

    # 5. Проверить, что бинарник запускается и это именно статическая LGPL-сборка.
    $banner = (& $exe -hide_banner -version) -join "`n"
    if ($LASTEXITCODE -ne 0) { throw "ffmpeg.exe -version завершился с кодом $LASTEXITCODE" }
    if ($banner -match '--enable-gpl' -or $banner -match '--enable-nonfree') { throw 'Это не LGPL-сборка FFmpeg (enable-gpl/nonfree).' }
    if ($banner -match '--enable-shared') { throw 'Получена shared-сборка FFmpeg (нужна статическая).' }
    Set-Content -Path $stamp -Value $asset.Sha -Encoding ascii
    Write-Host ($banner -split "`n" | Select-Object -First 1)
    Write-Host "Готово: $exe ($([math]::Round((Get-Item $exe).Length / 1MB, 1)) МБ)"
} catch {
    [Console]::Error.WriteLine("fetch_ffmpeg.ps1: ОШИБКА: $($_.Exception.Message)")
    exit 1
} finally {
    Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue
}
