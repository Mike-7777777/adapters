
# Added by the Harbor adapter. The verifier sends its request one second after
# `make start` launches the gateway, before an instrumented service on a busy host
# is listening. Each start recipe now returns once its port accepts connections.
# The probe is a bare TCP connect: an HTTP probe would record an extra trace.
cd /workdir
cat > wait_for_port.py <<'PY'
import socket
import sys
import time

port = int(sys.argv[1])
deadline = time.monotonic() + 120
while time.monotonic() < deadline:
    try:
        socket.create_connection(("localhost", port), timeout=1).close()
        sys.exit(0)
    except OSError:
        time.sleep(0.2)
sys.exit(f"port {port} did not open within 120 seconds")
PY
python3 - <<'PY'
import re
from pathlib import Path

PORTS = {"gateway": 8080, "stock": 8081, "order": 8082}
# The recipe line that starts the service in the background and records its PID.
LAUNCH = re.compile(r"&\s*echo\s+\\?\$\$!\s*>\s*\$\(PIDFILE\)")

for service, port in PORTS.items():
    makefile = Path(service) / "Makefile"
    lines = makefile.read_text().splitlines(keepends=True)
    launches = [i for i, line in enumerate(lines) if LAUNCH.search(line)]
    if len(launches) != 1:
        raise SystemExit(f"{makefile}: expected one launch line, found {len(launches)}")
    lines.insert(launches[0] + 1, f"\tpython3 ../wait_for_port.py {port}\n")
    makefile.write_text("".join(lines))
PY
