#!/bin/sh
set -e

PRIVATE_DIR="/var/www/filegator/private"
REPOSITORY_DIR="/var/www/filegator/repository"

echo "[init] Setting up FileGator..."

# 0. Ставим jq для безопасной работы с JSON на лету
if ! command -v jq >/dev/null 2>&1; then
    echo "[init] Installing jq..."
    apk add --no-cache jq || (apt-get update && apt-get install -y jq)
fi

# 1. Создаем структуру папок
mkdir -p "$PRIVATE_DIR/logs"
mkdir -p "$PRIVATE_DIR/cache"
mkdir -p /tmp/empty_dir_for_guest

ADMIN_HASH=$(php -r "echo password_hash('${FILEGATOR_ADMIN_PASS}', PASSWORD_DEFAULT);")
MANAGER_HASH=$(php -r "echo password_hash('${FILEGATOR_MANAGER_PASS}', PASSWORD_DEFAULT);")
# 2. Проверяем, существует ли users.json
# 2. Создаем базовый users.json, если он вообще отсутствует
if [ ! -f "$PRIVATE_DIR/users.json" ] || [ ! -s "$PRIVATE_DIR/users.json" ]; then
    echo "[init] Creating default users.json..."
    cat > "$PRIVATE_DIR/users.json" <<'EOF'
{
  "guest": {
    "username": "guest",
    "name": "Guest User",
    "role": "guest",
    "homedir": "/tmp/empty_dir_for_guest",
    "permissions": ""
  },
  "admin": {
    "username": "admin",
    "name": "Administrator",
    "password": "$ADMIN_HASH",
    "role": "admin",
    "homedir": "/",
    "permissions": "read|write|upload|download|batchdownload|zip"
  },
  "manager": {
    "username": "manager",
    "name": "Manager",
    "password": "$MANAGER_HASH",
    "role": "user",
    "homedir": "/manager",
    "permissions": "read|write|upload|download"
  }
}
EOF
fi
# 3. Динамическая обработка кастомного пользователя из ENV
if [ -n "$FILEGATOR_USER" ] && [ -n "$FILEGATOR_PASS" ]; then
    echo "[init] Processing user: $FILEGATOR_USER..."

    # Генерируем валидный bcrypt хэш средствами PHP (FileGator использует именно его)
    # Используем PASSWORD_DEFAULT (для PHP это bcrypt)
    HASHED_PASS=$(php -r "echo password_hash('${FILEGATOR_PASS}', PASSWORD_DEFAULT);")
    
    USER_HOMEDIR=${FILEGATOR_HOMEDIR:-"/"}

    # Создаем поддиректорию в репозитории, если её нет (например, /manager)
    if [ "$USER_HOMEDIR" != "/" ]; then
        mkdir -p "$REPOSITORY_DIR$USER_HOMEDIR"
    fi

    # С помощью jq аккуратно добавляем или обновляем блок пользователя в JSON
    # Это предотвратит затирание файла при рестарте и обновит пароль, если вы изменили ENV
    TMP_JSON=$(mktemp)
    jq --arg user "$FILEGATOR_USER" \
       --arg pass "$HASHED_PASS" \
       --arg home "$USER_HOMEDIR" \
       '.[$user] = {
         "username": $user,
         "name": $user,
         "role": "user",
         "homedir": $home,
         "permissions": "read|write|upload|download",
         "password": $pass
       }' "$PRIVATE_DIR/users.json" > "$TMP_JSON"
    
    mv "$TMP_JSON" "$PRIVATE_DIR/users.json"
    echo "[init] User $FILEGATOR_USER successfully configured/updated."
fi

# 4. Выставляем права владельца (www-data = 33:33)
chown 33:33 /tmp/empty_dir_for_guest

chown -R 33:33 "$PRIVATE_DIR"
chmod -R 755 "$PRIVATE_DIR"

chown -R 33:33 "$REPOSITORY_DIR"
chmod -R 755 "$REPOSITORY_DIR"

echo "[init] FileGator setup complete!"

# 5. Запуск веб-сервера
exec php -S 0.0.0.0:8080 -t /var/www/filegator/dist