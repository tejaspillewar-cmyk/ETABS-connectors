# Compiles EtabsGuiPlugin.dll with the C# compiler that ships inside
# Windows (.NET Framework 4) -- no Visual Studio or .NET SDK required.
#
# CSI's plugin loader accepts .NET Framework 4.6.1-4.8 or .NET Core/.NET
# 2.0-8.0, but explicitly rejects 32-bit assemblies, so we target x64 and
# stamp a 4.8 TargetFrameworkAttribute even though csc.exe itself is the
# older 4.0 compiler -- 4.0-4.8 all run on the same CLR (v4.0.30319), so the
# resulting DLL is a normal 4.8-compatible assembly.

$ErrorActionPreference = "Stop"

$csc = "C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
if (-not (Test-Path $csc)) {
    throw "csc.exe not found at $csc -- is .NET Framework 4.x installed?"
}

$etabsInstall = "C:\Program Files\Computers and Structures\ETABS 23"
$etabsDll = Join-Path $etabsInstall "ETABSv1.dll"
if (-not (Test-Path $etabsDll)) {
    throw "ETABSv1.dll not found at $etabsDll -- edit build.ps1 `$etabsInstall to match your ETABS install."
}

$fx = "C:\Windows\Microsoft.NET\Framework64\v4.0.30319"
$src = Join-Path $PSScriptRoot "EtabsGuiPlugin.cs"
$out = Join-Path $PSScriptRoot "EtabsGuiPlugin.dll"

& $csc `
    /nologo /target:library /platform:x64 /out:$out `
    /reference:$etabsDll `
    /reference:"$fx\System.dll" `
    /reference:"$fx\System.Windows.Forms.dll" `
    /reference:"$fx\mscorlib.dll" `
    $src

if ($LASTEXITCODE -ne 0) {
    throw "Build failed."
}

# ETABS hosts plugins on .NET 8, which resolves a plugin's dependencies
# relative to the plugin's own folder rather than ETABS's install folder or
# the GAC. CSI's docs say not to ship ETABSv1.dll alongside a *public*
# plugin (to avoid a stale copy lingering after an ETABS upgrade), but
# without admin rights `RegisterETABS.exe` can't register it system-wide,
# and this in-house plugin only ever runs on this machine -- so we copy the
# exact same-version DLL from the install folder on every build instead.
# If ETABS is upgraded, just re-run this script to refresh the copy.
Copy-Item $etabsDll (Join-Path $PSScriptRoot "ETABSv1.dll") -Force

Write-Output ""
Write-Output "Built: $out"
Write-Output "In ETABS: Tools > Add/Show Plugins > browse to this DLL > Add."
Write-Output "Edit plugin.config first if your python env or script path differ."
