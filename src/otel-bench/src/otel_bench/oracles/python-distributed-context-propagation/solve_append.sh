
# Added by the Harbor adapter. server.py calls server.shutdown() from its SIGTERM
# handler, on the thread that runs serve_forever(), which deadlocks. The verifier
# then reads an empty server_output.txt because stdout was never flushed. Without
# the call, sys.exit(0) in the handler stops the server and flushes the output.
sed -i '/^        server.shutdown()$/d' /workdir/server.py
if grep -q 'server.shutdown()' /workdir/server.py; then
  echo "server.py still calls server.shutdown()" >&2
  exit 1
fi
