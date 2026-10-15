# Flips the two real toggles in Windows Settings -> Personalization -> Taskbar, the way a
# person would, because Windows 11 ignores the old registry values. Run by taskbar_quiet.py.
#   -Want "badges=off,flashing=off"   what to set (on/off); a missing key is left alone
#   -Save <file>                      JSON of the values found BEFORE changing anything,
#                                     written first so a crash can still be undone
# Prints the values found, as JSON, on the last line.
param([string]$Want = "", [string]$Save = "")
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
$ids = @{ badges = "SystemSettings_Taskbar_Badging_CheckBox"; flashing = "SystemSettings_DesktopTaskbar_Flashing_CheckBox" }
$auto = [Windows.Automation.AutomationElement]
$toggle = [Windows.Automation.TogglePattern]::Pattern

function Find-Box($id) {
    $cond = New-Object Windows.Automation.PropertyCondition($auto::AutomationIdProperty, $id)
    $frames = $auto::RootElement.FindAll("Children", (New-Object Windows.Automation.PropertyCondition($auto::ClassNameProperty, "ApplicationFrameWindow")))
    foreach ($f in $frames) {
        $e = $f.FindFirst("Descendants", $cond)
        if ($e) { return @{ box = $e; frame = $f } }
    }
    return $null
}

Get-Process SystemSettings -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Milliseconds 300
Start-Process "ms-settings:taskbar"

$found = $null
for ($i = 0; $i -lt 60; $i++) {
    Start-Sleep -Milliseconds 250
    $found = Find-Box $ids["badges"]
    if ($found) { break }
}
if (-not $found) { Write-Output "{}"; exit 2 }

# Out of sight while it is used.
try {
    $tp = $found.frame.GetCurrentPattern([Windows.Automation.TransformPattern]::Pattern)
    $tp.Move(-20000, -20000)
} catch {}

$before = @{}
foreach ($k in $ids.Keys) {
    $f = Find-Box $ids[$k]
    if ($f) { $before[$k] = ($f.box.GetCurrentPattern($toggle).Current.ToggleState -eq "On") }
}

$wanted = @{}
foreach ($pair in ($Want -split ",")) {
    $kv = $pair -split "="
    if ($kv.Count -eq 2 -and $ids.ContainsKey($kv[0].Trim())) { $wanted[$kv[0].Trim()] = ($kv[1].Trim() -eq "on") }
}

if ($Save) {
    $toSave = @{}
    foreach ($k in $wanted.Keys) { if ($before.ContainsKey($k) -and $before[$k] -ne $wanted[$k]) { $toSave[$k] = $before[$k] } }
    if ($toSave.Count) { ($toSave | ConvertTo-Json -Compress) | Set-Content -Path $Save -Encoding UTF8 }
}

foreach ($k in $wanted.Keys) {
    $f = Find-Box $ids[$k]
    if (-not $f) { continue }
    $p = $f.box.GetCurrentPattern($toggle)
    if (($p.Current.ToggleState -eq "On") -ne $wanted[$k]) { $p.Toggle() }
}
Start-Sleep -Milliseconds 400
Get-Process SystemSettings -ErrorAction SilentlyContinue | Stop-Process -Force
Write-Output ($before | ConvertTo-Json -Compress)
