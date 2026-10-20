param(
    [ValidateSet('menu', 'save', 'list', 'verify', 'restore')]
    [string]$Action = 'menu',
    [string]$Version,
    [string]$Note = '',
    [switch]$Yes
)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.IO.Compression
$ProjectRoot = [System.IO.Path]::GetFullPath($PSScriptRoot).TrimEnd('\', '/')
$VersionRoot = Join-Path (Split-Path $ProjectRoot -Parent) '_versions'

function Get-ProjectFiles {
    function Visit-Directory([string]$Directory, [bool]$AtRoot) {
        foreach ($item in (Get-ChildItem -LiteralPath $Directory -Force)) {
            if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) { continue }
            if ($item.PSIsContainer) {
                if ($item.Name -in @('.git', '__pycache__', '.pytest_cache', 'node_modules', '_versions')) { continue }
                if ($item.Name -like '.venv*' -or $item.Name -like 'backup*') { continue }
                if ($AtRoot -and $item.Name -in @('sandbox', 'scripts', 'python_scripts')) { continue }
                Visit-Directory $item.FullName $false
            } else {
                if ($item.Name -match '^(\.env(\..*)?|desktop\.ini)$' -and $item.Name -ne '.env.example') { continue }
                if ($item.Name -match '\.(log|bak|pyc|pyo)$') { continue }
                [pscustomobject]@{
                    Path = $item.FullName.Substring($ProjectRoot.Length + 1).Replace('\', '/')
                    FullName = $item.FullName
                }
            }
        }
    }
    @(Visit-Directory $ProjectRoot $true) | Sort-Object Path
}

function Get-VersionNames {
    if (-not (Test-Path -LiteralPath $VersionRoot)) { return @() }
    @(Get-ChildItem -LiteralPath $VersionRoot -Directory |
        Where-Object { $_.Name -match '^v[0-9]{3,}$' } |
        Sort-Object Name |
        ForEach-Object { $_.Name })
}

function Get-VersionInfo([string]$Name) {
    if ($Name -notmatch '^v[0-9]{3,}$') { throw "无效版本号：$Name" }
    $folder = Join-Path $VersionRoot $Name
    $infoPath = Join-Path $folder 'manifest.json'
    $archivePath = Join-Path $folder 'project.zip'
    if (-not (Test-Path -LiteralPath $infoPath) -or -not (Test-Path -LiteralPath $archivePath)) {
        throw "版本文件不完整：$Name"
    }
    $info = Get-Content -LiteralPath $infoPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($info.version -cne $Name) { throw "版本清单不匹配：$Name" }
    [pscustomobject]@{ Info = $info; Archive = $archivePath }
}

function Assert-SafeRelativePath([string]$Path) {
    if ([string]::IsNullOrWhiteSpace($Path) -or $Path.StartsWith('/') -or
        $Path.Contains('\') -or $Path.Contains(':') -or
        @($Path.Split('/')) -contains '..' -or @($Path.Split('/')) -contains '.') {
        throw "版本中有不安全的路径：$Path"
    }
}

function Save-Version([string]$Description) {
    New-Item -ItemType Directory -Path $VersionRoot -Force | Out-Null
    $names = @(Get-VersionNames)
    $number = if ($names.Count -eq 0) { 1 } else { [int]($names[-1].Substring(1)) + 1 }
    $name = 'v{0:D3}' -f $number
    $temporary = Join-Path $VersionRoot ('_creating-' + $name + '-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $temporary | Out-Null
    $archivePath = Join-Path $temporary 'project.zip'
    $records = [System.Collections.Generic.List[object]]::new()
    try {
        $fileStream = [System.IO.File]::Open($archivePath, [System.IO.FileMode]::CreateNew)
        try {
            $zip = [System.IO.Compression.ZipArchive]::new($fileStream, [System.IO.Compression.ZipArchiveMode]::Create, $false, [System.Text.Encoding]::UTF8)
            try {
                foreach ($file in @(Get-ProjectFiles)) {
                    Assert-SafeRelativePath $file.Path
                    $entry = $zip.CreateEntry($file.Path, [System.IO.Compression.CompressionLevel]::Optimal)
                    $source = [System.IO.File]::OpenRead($file.FullName)
                    try {
                        $destination = $entry.Open()
                        try { $source.CopyTo($destination) } finally { $destination.Dispose() }
                    } finally { $source.Dispose() }
                    $records.Add([pscustomobject]@{
                        path = $file.Path
                        sha256 = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash
                        bytes = (Get-Item -LiteralPath $file.FullName).Length
                    })
                }
            } finally { $zip.Dispose() }
        } finally { $fileStream.Dispose() }
        $info = [ordered]@{
            version = $name
            createdAt = (Get-Date).ToString('o')
            note = $Description
            archiveSha256 = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash
            files = @($records.ToArray())
        }
        $info | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $temporary 'manifest.json') -Encoding UTF8
        [System.IO.Directory]::Move($temporary, (Join-Path $VersionRoot $name))
        Write-Host "已保存 $name（$($records.Count) 个文件）：$Description"
        return $name
    } catch {
        Write-Warning "保存未完成；临时文件保留在 $temporary"
        throw
    }
}

function Verify-Version([string]$Name) {
    $versionData = Get-VersionInfo $Name
    $info = $versionData.Info
    $actualArchiveHash = (Get-FileHash -LiteralPath $versionData.Archive -Algorithm SHA256).Hash
    if ($actualArchiveHash -cne $info.archiveSha256) { throw "$Name 压缩包校验失败" }
    $expected = @{}
    foreach ($record in @($info.files)) {
        $path = [string]$record.path
        Assert-SafeRelativePath $path
        if ($expected.ContainsKey($path)) { throw "$Name 有重复路径：$path" }
        $expected[$path] = $record
    }
    $seen = @{}
    $zip = [System.IO.Compression.ZipFile]::OpenRead($versionData.Archive)
    try {
        foreach ($entry in $zip.Entries) {
            $path = $entry.FullName
            Assert-SafeRelativePath $path
            if (-not $expected.ContainsKey($path) -or $seen.ContainsKey($path)) {
                throw "$Name 包含未登记或重复的文件：$path"
            }
            $stream = $entry.Open()
            $sha = [System.Security.Cryptography.SHA256]::Create()
            try { $hash = [System.BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-', '') }
            finally { $sha.Dispose(); $stream.Dispose() }
            if ($hash -cne $expected[$path].sha256 -or $entry.Length -ne $expected[$path].bytes) {
                throw "$Name 文件校验失败：$path"
            }
            $seen[$path] = $true
        }
    } finally { $zip.Dispose() }
    if ($seen.Count -ne $expected.Count) { throw "$Name 缺少文件" }
    Write-Host "$Name 校验通过（$($seen.Count) 个文件）"
    return $versionData
}

function Restore-Version([string]$Name) {
    $target = Verify-Version $Name
    if (-not $Yes) {
        $answer = Read-Host "即将回退到 $Name；输入 回退 继续"
        if ($answer -cne '回退') { Write-Host '已取消'; return }
    }
    $before = Save-Version "回退到 $Name 前自动保存"
    $stage = Join-Path $VersionRoot ('_restoring-' + [guid]::NewGuid().ToString('N'))
    $incoming = Join-Path $stage 'incoming'
    $previous = Join-Path $stage 'previous'
    New-Item -ItemType Directory -Path $incoming, $previous -Force | Out-Null
    $moved = [System.Collections.Generic.List[string]]::new()
    $installed = [System.Collections.Generic.List[string]]::new()
    try {
        $zip = [System.IO.Compression.ZipFile]::OpenRead($target.Archive)
        try {
            foreach ($entry in $zip.Entries) {
                Assert-SafeRelativePath $entry.FullName
                $destinationPath = Join-Path $incoming ($entry.FullName.Replace('/', '\'))
                New-Item -ItemType Directory -Path (Split-Path $destinationPath -Parent) -Force | Out-Null
                $sourceStream = $entry.Open()
                $destinationStream = [System.IO.File]::Create($destinationPath)
                try { $sourceStream.CopyTo($destinationStream) }
                finally { $destinationStream.Dispose(); $sourceStream.Dispose() }
            }
        } finally { $zip.Dispose() }
        foreach ($file in @(Get-ProjectFiles)) {
            $relative = $file.Path.Replace('/', '\')
            $oldPath = Join-Path $previous $relative
            New-Item -ItemType Directory -Path (Split-Path $oldPath -Parent) -Force | Out-Null
            [System.IO.File]::Move($file.FullName, $oldPath)
            $moved.Add($relative)
        }
        foreach ($record in @($target.Info.files)) {
            $relative = ([string]$record.path).Replace('/', '\')
            $destinationPath = Join-Path $ProjectRoot $relative
            New-Item -ItemType Directory -Path (Split-Path $destinationPath -Parent) -Force | Out-Null
            [System.IO.File]::Copy((Join-Path $incoming $relative), $destinationPath)
            $installed.Add($destinationPath)
        }
        Write-Host "已回退到 $Name。回退前状态保存在 $before。练习数据和 .env 未改动。"
    } catch {
        foreach ($path in $installed) { if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Force } }
        foreach ($relative in $moved) {
            $destinationPath = Join-Path $ProjectRoot $relative
            New-Item -ItemType Directory -Path (Split-Path $destinationPath -Parent) -Force | Out-Null
            [System.IO.File]::Move((Join-Path $previous $relative), $destinationPath)
        }
        Write-Warning "回退失败，已尝试恢复原文件；回退前快照为 $before，临时文件在 $stage"
        throw
    }
    $resolvedRoot = [System.IO.Path]::GetFullPath($VersionRoot).TrimEnd('\', '/') + [System.IO.Path]::DirectorySeparatorChar
    if (-not ([System.IO.Path]::GetFullPath($stage).StartsWith($resolvedRoot, [System.StringComparison]::OrdinalIgnoreCase))) {
        throw '临时目录位置异常，已停止清理'
    }
    Remove-Item -LiteralPath $stage -Recurse -Force
}

function Show-Versions {
    $names = @(Get-VersionNames)
    if ($names.Count -eq 0) { Write-Host '还没有快照'; return }
    foreach ($name in $names) {
        try {
            $info = (Get-VersionInfo $name).Info
            Write-Host ('{0}  {1}  {2}' -f $name, $info.createdAt, $info.note)
        } catch { Write-Warning "$name 文件不完整：$_" }
    }
}

if ($Action -eq 'menu') {
    while ($true) {
        Write-Host "`n项目：$ProjectRoot"
        Write-Host '1 保存当前版本   2 查看版本   3 校验版本   4 回退版本   0 退出'
        $choice = Read-Host '请选择'
        try {
            switch ($choice) {
                '1' { $description = Read-Host '这次改了什么（可留空）'; Save-Version $description | Out-Null }
                '2' { Show-Versions }
                '3' { $selected = Read-Host '输入版本号，例如 v001'; Verify-Version $selected | Out-Null }
                '4' { Show-Versions; $selected = Read-Host '输入要回退的版本号'; Restore-Version $selected }
                '0' { return }
                default { Write-Host '请输入 0—4' }
            }
        } catch { Write-Host "操作失败：$_" -ForegroundColor Red }
    }
}

switch ($Action) {
    'save' { Save-Version $Note | Out-Null }
    'list' { Show-Versions }
    'verify' { Verify-Version $Version | Out-Null }
    'restore' { Restore-Version $Version }
}
