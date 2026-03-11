#!/bin/sh
set -e

PRIVATE_DIR="/var/www/filegator/private"

echo "[init] Setting up FileGator..."

# 1. Создаем структуру папок
mkdir -p "$PRIVATE_DIR/logs"
mkdir -p "$PRIVATE_DIR/cache"

# 2. Проверяем, существует ли users.json
if [ ! -f "$PRIVATE_DIR/users.json" ]; then
    echo "[init] Creating users.json..."
    
    # Генерируем хэш пароля (заглушка, лучше задать вручную)
    # В продакшене используй переменную окружения
    cat > "$PRIVATE_DIR/users.json" <<'EOF'
{
  "guest": {
    "username": "guest",
    "name": "Guest User",
    "role": "guest",
    "homedir": "/",
    "permissions": "read"
  },
  "admin": {
    "username": "admin",
    "name": "Administrator",
    "password": "$2a$12$DRCtREKjLohB1yqD0mcvPuSM/NylTs2DP77S50j51.tIIjm03kRNW",
    "role": "admin",
    "homedir": "/",
    "permissions": "read|write|upload|download|batchdownload|zip"
  }
}
EOF
fi

# 3. Права владельца (www-data = 33:33)
chown -R 33:33 "$PRIVATE_DIR"
chmod -R 755 "$PRIVATE_DIR"

chown -R 33:33 /var/www/filegator/repository
chmod -R 755 /var/www/filegator/repository

echo "[init] FileGator setup complete!"

# 4. Запуск PHP сервера
exec php -S 0.0.0.0:8080 -t /var/www/filegator/dist