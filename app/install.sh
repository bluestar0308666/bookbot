#!/usr/bin/env bash
set -euo pipefail

SOURCE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
TARGET="/opt/bookbot"
BOOKBOT_USER="${BOOKBOT_USER:-${SUDO_USER:-$(id -un)}}"

if ! id "$BOOKBOT_USER" >/dev/null 2>&1; then
  echo "运行用户不存在：$BOOKBOT_USER"
  exit 1
fi

if [ ! -d "$SOURCE_DIR/library" ] || [ ! -f "$SOURCE_DIR/app/server_readonly.py" ]; then
  echo "安装包结构不完整。"
  exit 1
fi

BOOKBOT_GROUP="$(id -gn "$BOOKBOT_USER")"
sudo install -d -o "$BOOKBOT_USER" -g "$BOOKBOT_GROUP" "$TARGET/app" "$TARGET/library" "$TARGET/data" "$TARGET/backups"
if [ ! -f "$TARGET/library/assets/library.json" ]; then
  echo "未发现现有线上书架：$TARGET/library/assets/library.json"
  echo "此安装包只用于升级现有 BookBot，已停止以避免覆盖书库。"
  exit 1
fi
if [ -f "$TARGET/data/reader.db" ]; then
  sudo cp "$TARGET/data/reader.db" "$TARGET/backups/reader-$(date +%Y%m%d-%H%M%S).db"
elif [ -f "$SOURCE_DIR/seed/reader.db" ]; then
  sudo install -o "$BOOKBOT_USER" -g "$BOOKBOT_GROUP" -m 0600 "$SOURCE_DIR/seed/reader.db" "$TARGET/data/reader.db"
fi
# 只升级程序壳与 PDF.js；现有书架索引、旧版流式页和 books/ 永不覆盖或删除。
sudo rsync -a \
  --exclude 'books/' \
  --exclude 'assets/library.json' \
  --exclude 'assets/library.json.bak-*' \
  --exclude 'assets/book.json' \
  --exclude 'assets/conversion.json' \
  --exclude 'assets/pages/' \
  "$SOURCE_DIR/library/" "$TARGET/library/"
sudo install -m 0755 "$SOURCE_DIR/app/server_readonly.py" "$TARGET/app/server_readonly.py"
sudo install -m 0644 "$SOURCE_DIR/app/publish_api.py" "$TARGET/app/publish_api.py"
sed "s/__BOOKBOT_USER__/$BOOKBOT_USER/g" "$SOURCE_DIR/app/bookbot.service" | sudo tee /etc/systemd/system/bookbot.service >/dev/null

if [ ! -f /etc/bookbot.env ]; then
  read -r -p "私人书库用户名（默认 reader）：" READER_USER
  READER_USER="${READER_USER:-reader}"
  read -r -s -p "请设置私人书库访问密码：" READER_PASS
  echo
  if [ -z "$READER_PASS" ]; then
    echo "密码不能为空。"
    exit 1
  fi
  printf 'READER_USERNAME=%q\nREADER_PASSWORD=%q\n' "$READER_USER" "$READER_PASS" | sudo tee /etc/bookbot.env >/dev/null
  sudo chmod 600 /etc/bookbot.env
fi

if ! sudo grep -q '^BOOKBOT_PUBLISH_TOKEN=' /etc/bookbot.env; then
  PUBLISH_TOKEN="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
  printf 'BOOKBOT_PUBLISH_TOKEN=%s\n' "$PUBLISH_TOKEN" | sudo tee -a /etc/bookbot.env >/dev/null
  sudo chmod 600 /etc/bookbot.env
  echo "已生成发布令牌（BOOKBOT_PUBLISH_TOKEN），请填入本地 data/publish-config.json。"
fi

sudo chown -R "$BOOKBOT_USER:$BOOKBOT_GROUP" "$TARGET"
sudo systemctl daemon-reload
sudo systemctl enable --now bookbot.service
sudo systemctl restart bookbot.service
sudo systemctl --no-pager --full status bookbot.service | head -n 12
echo "本机服务地址：http://127.0.0.1:13011/"
echo "公网入口仍由 Caddy 的 3011 端口转发。"
echo "PaperBot 仍使用 3008，互不影响。"
echo "现有 reader.db 已保留；首次安装则使用安装包内的同步数据种子。"
