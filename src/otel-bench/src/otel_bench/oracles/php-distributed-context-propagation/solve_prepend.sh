# Added by the Harbor adapter. The upstream oracle draws traces.txt with python3,
# which the php:8.2-cli image does not ship; without it the script aborts and
# leaves its server and collector on the ports the verifier needs.
apt-get update > /dev/null
apt-get install -y python3 > /dev/null

