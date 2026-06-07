# TVController.ps1
# Runs as a scheduled task at logon (interactive session, highest privileges).
# Listens for system power events and coordinates TV on/off and display switching via a Raspberry Pi running server.py over HTTP.

Add-Type -AssemblyName System.Windows.Forms

# -------------------------
# CONFIGURATION
# -------------------------

# IP address and port of the Raspberry Pi running server.py
$global:piBaseUrl = "http://YOUR_PI_IP:5005"

# Wake source device IDs from powercfg -lastwake output.
# Run: powercfg -lastwake after waking from each source to find your values.
# CONTROLLER_WAKE_ID: the USB root port your controller is connected to
# DESK_WAKE_ID: the USB root port your keyboard/mouse is connected to
# If your keyboard and mouse are on different root ports, either pick one or
# add both IDs to the elseif condition in the Resume handler.

$global:controllerWakeId = "DEV_XXXX"   # e.g. DEV_14DB - controller USB root port (Living room htpc area)
$global:deskWakeId       = "DEV_XXXX"   # e.g. DEV_15B7 - keyboard/mouse USB root port (Desk area)

# How many seconds to wait after Pi responds before switching display to external.
# The TV needs time to wake and be EDID-ready. Increase if display switch is unreliable.
$global:edidWaitSeconds = 5

# -------------------------

$global:TVWasTurnedOn = $false

# Switches the PC display topology via Win32 SetDisplayConfig API.
# DisplaySwitch.exe is avoided because it triggers the Win+P overlay on Windows 11.
# SDC_TOPOLOGY_INTERNAL = 0x1, SDC_TOPOLOGY_EXTERNAL = 0x8, SDC_APPLY = 0x80
# Note: some online sources have CLONE and EXTERNAL swapped. The values below are correct.
function Invoke-DisplaySwitch {
    param([string]$Mode)
    if (-not ([System.Management.Automation.PSTypeName]'DisplayConfig').Type) {
        Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public class DisplayConfig {
    [DllImport("user32.dll")]
    public static extern int SetDisplayConfig(uint numPathArrayElements, IntPtr pathArray,
                                              uint numModeInfoArrayElements, IntPtr modeInfoArray,
                                              uint flags);
    public const uint SDC_TOPOLOGY_INTERNAL = 0x00000001;
    public const uint SDC_TOPOLOGY_EXTERNAL = 0x00000008;
    public const uint SDC_APPLY             = 0x00000080;
}
"@
    }
    $flags = if ($Mode -eq "/external") {
        [DisplayConfig]::SDC_TOPOLOGY_EXTERNAL -bor [DisplayConfig]::SDC_APPLY
    } else {
        [DisplayConfig]::SDC_TOPOLOGY_INTERNAL -bor [DisplayConfig]::SDC_APPLY
    }
    [DisplayConfig]::SetDisplayConfig(0, [IntPtr]::Zero, 0, [IntPtr]::Zero, $flags) | Out-Null
}

$handler = [Microsoft.Win32.PowerModeChangedEventHandler] {
    param($sender, $e)

    if ($e.Mode -eq [Microsoft.Win32.PowerModes]::Suspend) {
        if ($global:TVWasTurnedOn) {
            # Send tv-off multiple times asynchronously. The network adapter begins
            # suspending during this event, so a single synchronous call can be missed.
            # Using async calls gives the best chance of success.
            1..5 | ForEach-Object {
                try {
                    $wc = New-Object System.Net.WebClient
                    $wc.UploadStringAsync([Uri]"$global:piBaseUrl/tv-off", "POST", "")
                } catch {}
            }
            $global:TVWasTurnedOn = $false
        }
    }
    elseif ($e.Mode -eq [Microsoft.Win32.PowerModes]::Resume) {
        # Wait for network adapter to wake up before querying wake source or hitting the Pi.
        # Uncomment the larger sleep below if wake source is misidentified on your system.
        Start-Sleep -Seconds 2
        # Start-Sleep -Seconds 4

        $wakeInfo = powercfg -lastwake | Out-String

        if ($wakeInfo -match $global:controllerWakeId) {
            # Controller wake, turn TV on, switch display to external (TV)
            $piResponded = $false
            try {
                Invoke-RestMethod -Uri "$global:piBaseUrl/tv-on" -Method Post -TimeoutSec 5
                $piResponded = $true
            } catch {}

            if ($piResponded) {
                $global:TVWasTurnedOn = $true
                # First switch attempt. The TV may not be EDID-ready yet but it runs early to minimize time the monitor (at desk) is active. 
                # A second attempt follows after a longer wait to catch the switch once the TV is ready.
                Start-Sleep -Seconds $global:edidWaitSeconds
                Invoke-DisplaySwitch -Mode "/external"
                Start-Sleep -Seconds 10
                Invoke-DisplaySwitch -Mode "/external"
            }
        } elseif ($wakeInfo -match $global:deskWakeId -or $wakeInfo -match "Fixed Feature Power Button") {
            # Keyboard, mouse, or power button wake = switch display to internal (monitor)
            $global:TVWasTurnedOn = $false
            try { Invoke-DisplaySwitch -Mode "/internal" } catch {}
        } else {
            # Unknown wake source = default to internal display as safe fallback
            $global:TVWasTurnedOn = $false
            try { Invoke-DisplaySwitch -Mode "/internal" } catch {}
        }
    }
}

# Handles shutdown and restart. PowerModeChanged only fires on sleep, not shutdown.
# $global: scope is required for all variables used inside .NET event handler callbacks.
$sessionEndHandler = [Microsoft.Win32.SessionEndingEventHandler] {
    param($sender, $e)
    if ($global:TVWasTurnedOn) {
        try {
            Invoke-RestMethod -Uri "$global:piBaseUrl/tv-off" -Method Post -TimeoutSec 3
        } catch {}
        $global:TVWasTurnedOn = $false
    }
}

[Microsoft.Win32.SystemEvents]::add_PowerModeChanged($handler)
[Microsoft.Win32.SystemEvents]::add_SessionEnding($sessionEndHandler)
[System.Windows.Forms.Application]::Run()
