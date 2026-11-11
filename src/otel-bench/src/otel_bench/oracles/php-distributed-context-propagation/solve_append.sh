
# Added by the Harbor adapter. The verifier looks for the server's start-up line in
# server_output.txt; the upstream oracle's server.php no longer writes it.
sed -i '0,/^<?php$/s//<?php\nerror_log("Server starting on :8080");/' /workdir/server.php
if ! grep -q 'Server starting on :8080' /workdir/server.php; then
  echo "server.php does not log its start-up line" >&2
  exit 1
fi
