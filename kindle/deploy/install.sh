#!/bin/sh
# Installs the Kindle panel as the systemd service `kindle`, running from this checkout as the current user
# (idempotent; needs sudo). Fill in kindle/.env first (cp .env.example .env).
#   deploy/install.sh            the service
#   deploy/install.sh --mermaid  also mermaid-cli, so diagrams in Claude's replies show as images (needs npm and Chrome)
set -eu
dir=$(cd "$(dirname "$0")/.." && pwd)
[ -f "$dir/.env" ] || echo "warning: no $dir/.env; copy .env.example and fill it in" >&2

# PyJWT (Cloudflare Access tokens) and Pillow (PDF pages and diagrams as images); poppler-utils draws PDF pages.
if command -v apt-get >/dev/null; then
  sudo apt-get install -y --no-install-recommends python3-jwt python3-cryptography python3-pil poppler-utils
fi

if [ "${1:-}" = "--mermaid" ]; then
  mmd=$(cd "$dir" && python3 -c 'from ink import config; print(config.MERMAID_DIR)')
  mkdir -p "$mmd"
  [ -f "$mmd/package.json" ] || echo '{"private":true}' > "$mmd/package.json"
  (cd "$mmd" && PUPPETEER_SKIP_DOWNLOAD=1 npm install --silent @mermaid-js/mermaid-cli)
  chrome=$(command -v google-chrome || command -v chromium || command -v chromium-browser)
  echo "{\"executablePath\": \"$chrome\", \"args\": [\"--no-sandbox\", \"--headless=new\"]}" > "$mmd/puppeteer.json"
fi

sed -e "s|@USER@|$(id -un)|" -e "s|@KINDLE_DIR@|$dir|g" "$dir/deploy/kindle.service" \
  | sudo tee /etc/systemd/system/kindle.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable -q kindle
sudo systemctl restart kindle
echo "kindle: $(cd "$dir" && python3 -c 'from ink import config; print("panel on %s:%s, mic on %s:%s" % (*config.LISTEN, *config.MIC_LISTEN))')"
