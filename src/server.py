#!/usr/bin/env python3
# server.py
# Raspberry Pi HTTP server that receives commands from TVController.ps1
# and translates them into HDMI-CEC commands via cec-client (libCEC).
# Runs as a systemd service. Logs to stdout (captured by journald).

import http.server, subprocess, threading, json, time, logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
log = logging.getLogger(__name__)

# -------------------------
# CONFIGURATION
# -------------------------

# Port the HTTP server listens on. It must match piBaseUrl in TVController.ps1.
PORT = 5005

# This is the name that is given to the tv for the active source cec device. It flashes on the TV's osd.
# This will name the input that your pc is connected to, not your pi. Max 14 characters.
CEC_OSD_NAME = "Desktop"

# Physical address of the PC's HDMI input on the TV in CEC format.
# Format: X.0.0.0 where X is the HDMI port number the PC is connected to.
# e.g. HDMI 4 = 4.0.0.0 = 40:00 in the tx command below.
# To change it: replace the value below with your port's address.
# HDMI 1 = 10:00, HDMI 2 = 20:00, HDMI 3 = 30:00, HDMI 4 = 40:00
# If you have an AVR or soundbar between the TV and PC, run: echo "scan" | cec-client -d 1 -s
# to find the exact physical address reported for your device.
PC_HDMI_PORT_ADDRESS = "40:00"  # HDMI 4

# Seconds to wait after 'on 0' before sending first active source command.
# The TV needs time to wake and become CEC-ready before it will accept input switching.
# Increase if the TV wakes but does not switch to the PC input reliably.
CEC_WAKE_WAIT = 2

# Number of active source retry attempts after wake.
# The retries compensate for the TV being slow to accept CEC commands after waking.
CEC_ACTIVE_SOURCE_RETRIES = 4

# CEC device path.
# Pi 3B and earlier: /dev/cec0 (default)
# Pi 4B and later: try /dev/cec1 if /dev/cec0 fails
CEC_DEVICE = "/dev/cec0"

# -------------------------

class CECController:
    def __init__(self):
        self.lock = threading.Lock()

    def _start_cec(self):
        # A fresh cec-client process is spawned for every command rather than keeping a persistent connection.
        # This is intentional. The Pi's built-in /dev/cec0 loses its CEC bus session when the TV enters standby,
        # leaving a persistent process alive but unable to send commands. 
        # Spawning fresh each time guarantees a clean bus connection regardless of the TV state.

        # Kill any existing cec-client process to release /dev/cec0.
        # Without this, the new process gets errno=16 (device busy).
        # A short sleep allows the kernel time to fully release the device handle.
        log.info("Starting cec-client")
        subprocess.run(['killall', 'cec-client'], stderr=subprocess.DEVNULL)
        time.sleep(0.5)
        proc = subprocess.Popen(
            ['cec-client', '-d', '1', '-o', CEC_OSD_NAME, CEC_DEVICE],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT
        )
        ready = threading.Event()

        def drain():
            for raw in proc.stdout:
                if b'waiting for input' in raw:
                    ready.set()

        # cec-client takes ~2 seconds to negotiate the CEC bus and reach ready state.
        # Commands written before this point are silently ignored.
        # The drain thread watches stdout and sets the event when cec-client is ready.
        threading.Thread(target=drain, daemon=True).start()
        if ready.wait(timeout=5):
            log.info("cec-client ready")
        else:
            log.warning("cec-client did not reach ready state within timeout")
        return proc

    def _write(self, proc, cmd):
        proc.stdin.write((cmd + '\n').encode())
        proc.stdin.flush()

    def _tv_on(self):
        with self.lock:
            log.info("TV on: sending wake command")
            proc = self._start_cec()
            # Wake TV
            self._write(proc, 'on 0')
            time.sleep(CEC_WAKE_WAIT)
            # Send active source command multiple times - the TV may not be CEC-ready immediately after waking.
            # Retrying gives the TV time to become CEC-ready and accept the input switch.
            # tx 1F:82:XX:00 = active source broadcast for physical address X.0.0.0
            for i in range(CEC_ACTIVE_SOURCE_RETRIES):
                log.info(f"TV on: sending active source (attempt {i + 1}/{CEC_ACTIVE_SOURCE_RETRIES})")
                self._write(proc, f'tx 1F:82:{PC_HDMI_PORT_ADDRESS}')
                time.sleep(2)
            proc.kill()
            proc.wait()
            log.info("TV on: complete")

    def _tv_off(self):
        with self.lock:
            log.info("TV off: sending standby command")
            proc = self._start_cec()
            self._write(proc, 'standby 0')
            time.sleep(1)
            proc.kill()
            proc.wait()
            log.info("TV off: complete")

    def tv_on(self):
        threading.Thread(target=self._tv_on, daemon=True).start()

    def tv_off(self):
        threading.Thread(target=self._tv_off, daemon=True).start()

cec = CECController()

class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def respond(self, code, body):
        b = json.dumps(body).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', len(b))
        self.end_headers()
        self.wfile.write(b)

    def do_POST(self):
        if self.path == '/tv-on':
            log.info("Request received: /tv-on")
            self.respond(200, {'status': 'ok'})
            cec.tv_on()
        elif self.path == '/tv-off':
            log.info("Request received: /tv-off")
            self.respond(200, {'status': 'ok'})
            cec.tv_off()
        else:
            self.respond(404, {'status': 'error'})

    def do_GET(self):
        if self.path == '/status':
            self.respond(200, {'status': 'ok'})
        else:
            self.respond(404, {'status': 'error'})

log.info(f"TVController starting on port {PORT}")
http.server.ThreadingHTTPServer(('0.0.0.0', PORT), Handler).serve_forever()
