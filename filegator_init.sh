#!/bin/sh
set -e

PRIVATE_DIR="/var/www/filegator/private"
REPOSITORY_DIR="/var/www/filegator/repository"

echo "[init] Setting up FileGator..."

mkdir -p "$PRIVATE_DIR/logs" "$PRIVATE_DIR/cache" /tmp/empty_dir_for_guest "$REPOSITORY_DIR/manager"

ADMIN_PASS="${FILEGATOR_ADMIN_PASS:-admin_secret_pass}"
MANAGER_PASS="${FILEGATOR_MANAGER_PASS:-manager_secret_pass}"

php -r '
$privateDir = $argv[1];
$adminPass = $argv[2];
$managerPass = $argv[3];
$file = $privateDir . "/users.json";

$adminHash = password_hash($adminPass, PASSWORD_DEFAULT);
$managerHash = password_hash($managerPass, PASSWORD_DEFAULT);

$users = [
    "guest" => [
        "username" => "guest",
        "name" => "Guest User",
        "role" => "guest",
        "homedir" => "/tmp/empty_dir_for_guest",
        "permissions" => ""
    ],
    "admin" => [
        "username" => "admin",
        "name" => "Administrator",
        "role" => "admin",
        "homedir" => "/",
        "permissions" => "read|write|upload|download|batchdownload|zip",
        "password" => $adminHash
    ],
    "manager" => [
        "username" => "manager",
        "name" => "Manager",
        "role" => "user",
        "homedir" => "/manager",
        "permissions" => "read|write|upload|download",
        "password" => $managerHash
    ]
];

file_put_contents($file, json_encode($users, JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES));
echo "[init] users.json successfully created/updated\n";
' "$PRIVATE_DIR" "$ADMIN_PASS" "$MANAGER_PASS"

chown -R 33:33 "$PRIVATE_DIR" "$REPOSITORY_DIR" /tmp/empty_dir_for_guest
chmod -R 755 "$PRIVATE_DIR" "$REPOSITORY_DIR"

echo "[init] FileGator setup complete!"

exec php -S 0.0.0.0:8080 -t /var/www/filegator/dist