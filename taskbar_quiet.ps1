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
    $e = $script:frame.FindFirst("Descendants", $cond)
    if ($e) { return @{ box = $e } }
    return $null
}

Add-Type @"
using System; using System.Text; using System.Runtime.InteropServices;
public class SettingsWin {
  delegate bool EnumProc(IntPtr h, IntPtr l);
  [DllImport("user32.dll")] static extern bool EnumWindows(EnumProc p, IntPtr l);
  [DllImport("user32.dll")] static extern int GetClassName(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll")] static extern bool SetWindowPos(IntPtr h, IntPtr a, int x, int y, int w, int hh, uint f);
  [DllImport("user32.dll", EntryPoint = "GetWindowLongPtrW")] static extern IntPtr GetLong(IntPtr h, int i);
  [DllImport("user32.dll", EntryPoint = "SetWindowLongPtrW")] static extern IntPtr SetLong(IntPtr h, int i, IntPtr v);
  [DllImport("user32.dll")] static extern bool SetLayeredWindowAttributes(IntPtr h, uint key, byte alpha, uint flags);
  // The Settings window, found by its frame class and title.
  public static IntPtr Find() {
    IntPtr found = IntPtr.Zero;
    EnumWindows((h, l) => {
      var c = new StringBuilder(256); GetClassName(h, c, 256);
      if (c.ToString() == "ApplicationFrameWindow" && IsWindowVisible(h)) {
        var t = new StringBuilder(256); GetWindowText(h, t, 256);
        if (t.ToString() == "Settings") found = h;
      }
      return true;
    }, IntPtr.Zero);
    return found;
  }
  // Fully see-through and click-through (LAYERED | TRANSPARENT, alpha 0). Takes effect at once, far
  // sooner than moving the window does, so it is never seen while it opens.
  public static void Invisible(IntPtr h) {
    long ex = (long)GetLong(h, -20);
    SetLong(h, -20, (IntPtr)(ex | 0x80000 | 0x20));
    SetLayeredWindowAttributes(h, 0, 0, 2);
  }
  // Far off every screen, without resizing or activating it (NOSIZE | NOZORDER | NOACTIVATE).
  public static void MoveAway(IntPtr h) { SetWindowPos(h, IntPtr.Zero, -32000, -32000, 0, 0, 0x0001 | 0x0004 | 0x0010); }
}
"@

Get-Process SystemSettings -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Milliseconds 300
Start-Process "ms-settings:taskbar"

# Poll fast; make the window see-through, then move it away, the moment it exists.
$handle = [IntPtr]::Zero
$clock = [Diagnostics.Stopwatch]::StartNew()
while ($clock.ElapsedMilliseconds -lt 15000) {
    $handle = [SettingsWin]::Find()
    if ($handle -ne [IntPtr]::Zero) { [SettingsWin]::Invisible($handle); [SettingsWin]::MoveAway($handle); break }
    Start-Sleep -Milliseconds 4
}
if ($handle -eq [IntPtr]::Zero) { Write-Output "{}"; exit 2 }
$frame = $auto::FromHandle($handle)

# Settings puts itself back to its remembered spot as it finishes opening: keep pushing it away.
for ($i = 0; $i -lt 120; $i++) { [SettingsWin]::MoveAway($handle); Start-Sleep -Milliseconds 5 }

# The controls take a moment to load inside the (already hidden) window.
$found = $null
$cond = New-Object Windows.Automation.PropertyCondition($auto::AutomationIdProperty, $ids["badges"])
for ($i = 0; $i -lt 80; $i++) {
    [SettingsWin]::MoveAway($handle)
    if ($frame.FindFirst("Descendants", $cond)) { $found = $true; break }
    Start-Sleep -Milliseconds 100
}
if (-not $found) { Get-Process SystemSettings -ErrorAction SilentlyContinue | Stop-Process -Force; Write-Output "{}"; exit 2 }

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
