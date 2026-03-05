#!/bin/sh
set -e

DB_PATH="${FB_DATABASE:-/config/database.db}"
CONFIG_PATH="${FB_CONFIG:-/config/settings.json}"

# Если БД не существует — инициализируем
if [ ! -f "$DB_PATH" ]; then
    echo "[init] Database not found, initializing..."
    filebrowser config init -c "$CONFIG_PATH" -d "$DB_PATH"
    
    # Создаём пользователя, если заданы переменные
    if [ -n "$FB_ADMIN_USER" ] && [ -n "$FB_ADMIN_PASSWORD" ]; then
        echo "[init] Creating admin user: $FB_ADMIN_USER"
        filebrowser users add "$FB_ADMIN_USER" "$FB_ADMIN_PASSWORD" \
            --perm.admin -c "$CONFIG_PATH" -d "$DB_PATH"
    fi
fi

# Запускаем основной процесс filebrowser
echo "[start] Launching filebrowser..."
exec filebrowser -c "$CONFIG_PATH" -d "$DB_PATH"