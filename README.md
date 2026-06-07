# rpi-cec-bridge
A Raspberry Pi CEC bridge for hybrid desk/HTPC setups - Automatically turns the TV on/off and switches inputs based on how the PC was woken

> **This is an experimental project shared as-is.** It works reliably on the hardware it was built for (requires Windows), but it is not a polished product. Expect some configuration effort, and it may require hardware-specific tuning and fiddling to get it working.

---

## What this is

This project automates TV control for people who use a single PC in two different setups: at a desk with a monitor, and from a couch with a TV.

Press a wake-capable HTPC remote/controller - the TV turns on, switches to your PC's HDMI input, and your display switches to the TV. Put the PC to sleep, the TV turns off. Wake from the keyboard at your desk, the display switches back to the monitor. 

No TV remote needed.

--- 

## Why it exists

### Problem 1 - CEC doesn't work from a PC

HDMI-CEC lets devices on the same HDMI bus control each other (e.g., a game console can turn on your TV, switch inputs, and turn it off). Almost no PC GPU has hardware support for CEC. And even if it did, Windows has no API for it. This project solves that by using a Raspberry Pi as a CEC bridge. The Pi sits on the TV's HDMI bus, the PC sends HTTP requests to the Pi, and the Pi translates them into CEC commands.

### Problem 2 - The hybrid setup problem

Windows has no native concept of "I'm at my desk now" vs "I'm at my TV now" (no OS does). Yet people still do it. Display switching is manual (Win+P), and there's nothing that automatically turns the TV on or off based on how you woke the PC. The Pi handles that by using the wake source device ID to determine which context you're in.

---

## Is this for you?

This project is built for a specific setup:

- One PC, one desk monitor, one TV (Behavior with additional monitors is untested)
- The desk monitor doesn't need CEC - it can be DisplayPort, or HDMI without CEC concerns
- The TV does need CEC - that's how the Pi controls it
- You want the whole thing to be automatic, not manual

If that describes your setup, this is for you.

If you have two monitors and no TV, or a single display (without CEC), this doesn't apply. If you want to use this with two TVs, it's technically possible with two Pis but that's untested and more complex.

---

## Hardware requirements

- Windows PC with a desk monitor and a TV connected via HDMI (Windows 10 or later)
- Raspberry Pi connected to the TV via HDMI (a second HDMI port, separate from the PC)
  - Tested on: Pi 3B
  - Should work on: Pi 2B, 3B+, 4B, 5, Zero 2W
  - Does not work on: original Pi Zero (no CEC hardware)
- TV with HDMI-CEC enabled
- Wake-capable HTPC remote/controller
- Pi and PC on the same local network

---

## How it works

### Wake source detection
Windows assigns each USB root hub its own device instance ID. When the PC wakes, `powercfg -lastwake` reports which device triggered it. Your HTPC remote/controller and desk keyboard are on different hubs (if they aren't, put them on different root hubs), so they produce different IDs. This creates a unique wake ID for your HTPC remote/controller that allows the running service to differentiate the two setups. TV wake source = TV mode. Everything else = desk mode.

### Two components

**`TVController.ps1`** runs on the PC as a scheduled task at logon. On wake from sleep, it checks the wake source ID. If it matches the TV wake source (`controllerWakeId` in the script), it tells the Pi to turn the TV on and switches the display to external. On sleep, it tells the Pi to turn the TV off. On desk wake, it switches back to the monitor.

**`server.py`** runs on the Pi as a systemd service. It receives HTTP requests from the PC and sends them as CEC commands. `/tv-on` wakes the TV and switches to the PC's input. `/tv-off` puts it in standby.

> Only works from sleep (S3). The PC must be sleeping, not shut down. Waking from shutdown requires a manual logon first.

<details>
<summary>Installation</summary>
  
<details>
<summary>Raspberry Pi setup</summary>

### 1. Install Raspberry Pi OS

Use Raspberry Pi Imager. Raspberry Pi OS Lite (no desktop) is sufficient.

- Tested on: **Raspberry Pi OS Lite 64-bit (Bookworm)** (see [Hardware requirements](#hardware-requirements) for other supported devices)

### 2. Assign a static IP

Required. Use a DHCP reservation in your router settings.

### 3. Enable CEC on the TV

Find CEC in your TV settings and turn it on. May be called Anynet+, Bravia Sync, SimpLink, EasyLink, etc.

### 4. Install cec-utils

```bash
sudo apt update && sudo apt install cec-utils
```

Verify your TV shows up:

```bash
echo "scan" | cec-client -d 1 -s
```

### 5. Deploy server.py

Copy to `/home/pi/tvcontroller/server.py` and edit the config block at the top. Everything is documented in the file.

### 6. Create the systemd service

Create `/etc/systemd/system/tvcontroller.service`:

```ini
[Unit]
Description=TV Controller HTTP Server
After=network.target

[Service]
# Update this path if your username isn't pi
ExecStart=/usr/bin/python3 /home/pi/tvcontroller/server.py
Restart=always
# Change this to your username if different
User=pi

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable tvcontroller
sudo systemctl start tvcontroller
```

Verify it's running:

```bash
sudo systemctl status tvcontroller
```

> **Pi 4 note** - if `/dev/cec0` fails, try `/dev/cec1` instead. Change `CEC_DEVICE` in the config block of `server.py`.
</details>

<details>
<summary>PC side setup</summary>

### 1. Deploy TVController.ps1

Place the file somewhere permanent. Edit the config block at the top - everything is documented in the file.

### 2. Find your wake source device IDs

Wake the PC with your HTPC remote/controller, then run in PowerShell:

```powershell
powercfg -lastwake
```

Look for `DEV_XXXX` in the instance path. That's your `controllerWakeId`. Repeat from a keyboard wake for `deskWakeId`. Put both in the config block at the top of `TVController.ps1`.

To figure out which physical USB port maps to which `DEV_XXXX`, check your motherboard manual (section Block Diagram/IO Panel). Your HTPC device and desk keyboard need to be on different root hubs — if they're not, move one to a port on a different root hub. (You can experiment until you find a port that has a different ID)

If your device wakes via Bluetooth, use the Bluetooth adapter's device ID instead. (it will appear in powercfg -lastwake after waking from Bluetooth)

### 3. Create the scheduled task

Create the task from PowerShell directly (run as administrator):

```powershell
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-WindowStyle Hidden -NonInteractive -File `"C:\Path\To\TVController.ps1`""
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Days 0)
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -RunLevel Highest
Register-ScheduledTask -TaskName "TVController" -Action $action -Trigger $trigger -Settings $settings -Principal $principal
```
<details>
<summary>or, manually via Task Scheduler</summary>

- **General**: Run only when user is logged on, run with highest privileges
- **Triggers**: At log on
- **Actions**: `powershell.exe` with arguments `-WindowStyle Hidden -NonInteractive -File "C:\Path\To\TVController.ps1"`

</details>

To restart manually:

```powershell
Stop-ScheduledTask -TaskName "TVController"
Start-ScheduledTask -TaskName "TVController"
```
</details>

<details>
<summary>Testing, Tuning, and Known Limitations</summary>

### Testing

With the TV off, SSH into the Pi and run:

```bash
curl -X POST http://localhost:5005/tv-on
```

TV should turn on and switch to the PC's input. Then:

```bash
curl -X POST http://localhost:5005/tv-off
```

TV should go to standby. Run tv-on again without restarting the service - this is the important test.

Full flow:

1. Put PC to sleep
2. Wake with HTPC remote/controller - TV on, switches to PC input, display switches to external
3. Sleep again - TV off
4. Wake with keyboard - display switches back to monitor

Check Pi logs:

```bash
sudo journalctl -u tvcontroller.service -n 50
```


### Tuning

**TV wakes but doesn't switch to PC input** - increase `CEC_WAKE_WAIT` or `CEC_ACTIVE_SOURCE_RETRIES` in `server.py`.

**Display switches before TV is ready** - increase `$global:edidWaitSeconds` in `TVController.ps1`.

**Wrong wake source detected** - run `powercfg -lastwake` after each wake type and re-check your device IDs.

**TV turns on but display doesn't switch** - check that the Pi responded successfully. If `$piResponded` is false (Pi unreachable), the display switch is intentionally skipped to avoid a black screen.

### Known limitations

**Full power-off** - if the TV is fully powered off (not standby), the input switch may not work. CEC timing limitation.

</details>
</details>

---
## Q&A

**Why not just buy a Pulse-Eight CEC adapter?**

Maybe you already own a Pi. Or, if your GPU and TV support 4K120 or VRR, the Pulse-Eight tops out at HDMI 2.0, sits inline with the HDMI signal, and caps your bandwidth at 18Gbps instead of 48Gbps. And the Pulse-Eight doesn't solve the dual-setup problem. You still need something to detect the wake source and switch the display. The Pi handles both.

---

## Notes

**Home Assistant** - if you're running Home Assistant in a container on the Pi, `/dev/cec0` access requires passing the device through to the container. Possible but adds overhead.

**Persistent CEC connection** - you would expect the Pi to stay on the CEC bus permanently, like a regular CEC device would (Nvidia Shield / Xbox). The Pi's `/dev/cec0` drops its session every time the TV goes to standby on some TVs. Feel free to attempt to use a persistent CEC connection (remove the killall and _start_cec spawn logic and keep a single cec-client process alive in `CECController.__init__` and add a watchdog to monitor stdout and respawn when the connection dies)

---

## License

MIT
