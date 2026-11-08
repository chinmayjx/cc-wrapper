#!/bin/sh
# Optional: installs or updates the Whisper server for Kindle dictation on a machine with an NVIDIA GPU, over ssh.
# That machine runs it from its own clone of this repo, so updating is a pull and a restart.
#   deploy/stt-server.sh user@gpu-box [repo dir there, default ~/code/cc-wrapper]
# It listens on the machine's Tailscale address, port 8793: set KINDLE_WHISPER_URL=http://<that address>:8793.
set -e
host=${1:?usage: stt-server.sh user@host [repo dir]}
dir=${2:-'~/code/cc-wrapper'}
ssh "$host" "set -e
  dir=$dir; dir=\$(eval echo \$dir)
  [ -d \"\$dir\" ] || git clone -q https://github.com/chinmayjx/cc-wrapper \"\$dir\"
  git -C \"\$dir\" pull -q --ff-only
  venv=\$HOME/.venvs/whisper
  [ -x \$venv/bin/python ] || python3 -m venv \$venv
  \$venv/bin/pip install -q faster-whisper nvidia-cublas-cu12 'nvidia-cudnn-cu12==9.*'
  sed -e \"s|@USER@|\$(id -un)|\" -e \"s|@KINDLE_DIR@|\$dir/kindle|\" -e \"s|@VENV@|\$venv|\" -e 's|@LISTEN@|tailscale ip -4|' \
    \"\$dir/kindle/deploy/kindle-stt.service\" | sudo tee /etc/systemd/system/kindle-stt.service >/dev/null
  sudo systemctl daemon-reload && sudo systemctl enable -q kindle-stt && sudo systemctl restart kindle-stt"
echo "kindle-stt on $host: http://$(ssh "$host" 'tailscale ip -4'):8793/health"
