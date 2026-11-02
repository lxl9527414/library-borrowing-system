#!/usr/bin/env bash
set -Eeuo pipefail

: "${ADMIN_PASSWORD:?ADMIN_PASSWORD is required}"
: "${ADMIN_PASSWORD_HASH:?ADMIN_PASSWORD_HASH is required}"
: "${READER_PASSWORD:?READER_PASSWORD is required}"
: "${READER_PASSWORD_HASH:?READER_PASSWORD_HASH is required}"
: "${MYSQL_ROOT_PASSWORD:?MYSQL_ROOT_PASSWORD is required}"
: "${MYSQL_APP_PASSWORD:?MYSQL_APP_PASSWORD is required}"
: "${REDIS_PASSWORD:?REDIS_PASSWORD is required}"

APP_DIR=/opt/library
JAVA_URL="https://mirrors.tuna.tsinghua.edu.cn/Adoptium/17/jdk/x64/linux/OpenJDK17U-jdk_x64_linux_hotspot_17.0.20.1_1.tar.gz"
REDIS_URL="https://github.com/redis/redis/archive/refs/tags/7.4.2.tar.gz"
MYSQL_REPO_URL="https://repo.mysql.com/mysql80-community-release-el7-11.noarch.rpm"

log() {
    printf '[library-deploy] %s\n' "$*"
}

wait_for_mysql() {
    for _ in $(seq 1 40); do
        if mysqladmin -uroot ping >/dev/null 2>&1; then
            return 0
        fi
        sleep 1
    done
    return 1
}

wait_for_redis() {
    for _ in $(seq 1 20); do
        if "$APP_DIR/redis/bin/redis-cli" -a "$REDIS_PASSWORD" ping 2>/dev/null | grep -q PONG; then
            return 0
        fi
        sleep 1
    done
    return 1
}

wait_for_app() {
    for _ in $(seq 1 60); do
        if curl -fsS http://127.0.0.1:18080/ >/dev/null 2>&1; then
            return 0
        fi
        sleep 2
    done
    return 1
}

log "Installing build prerequisites"
yum install -y gcc make curl tar gzip openssl

log "Installing Java 17"
mkdir -p "$APP_DIR/java"
if [ ! -x "$APP_DIR/java/bin/java" ]; then
    curl -L --retry 3 --fail -o /tmp/library-jdk17.tar.gz "$JAVA_URL"
    tar -xzf /tmp/library-jdk17.tar.gz -C "$APP_DIR/java" --strip-components=1
fi
"$APP_DIR/java/bin/java" -version

log "Installing MySQL 8"
if ! rpm -q mysql80-community-release >/dev/null 2>&1; then
    yum install -y "$MYSQL_REPO_URL"
fi
yum install -y mysql-community-server

log "Configuring MySQL for a small server"
install -m 0644 /tmp/library-deploy/conf/mysql-library.cnf /etc/my.cnf.d/zz-library.cnf
if ! grep -q '^!includedir /etc/my.cnf.d' /etc/my.cnf; then
    printf '\n!includedir /etc/my.cnf.d\n' >> /etc/my.cnf
fi

if [ ! -d /var/lib/mysql/mysql ]; then
    mkdir -p /var/log/mysql
    chown mysql:mysql /var/log/mysql /var/lib/mysql
    log "Initializing MySQL data directory"
    mysqld --initialize-insecure --user=mysql --datadir=/var/lib/mysql
fi

systemctl enable mysqld
systemctl start mysqld
wait_for_mysql

log "Creating database and application user"
mysql -uroot <<SQL
ALTER USER 'root'@'localhost' IDENTIFIED BY '${MYSQL_ROOT_PASSWORD}';
CREATE DATABASE IF NOT EXISTS library_db CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS 'library_app'@'localhost' IDENTIFIED BY '${MYSQL_APP_PASSWORD}';
ALTER USER 'library_app'@'localhost' IDENTIFIED BY '${MYSQL_APP_PASSWORD}';
GRANT ALL PRIVILEGES ON library_db.* TO 'library_app'@'localhost';
DELETE FROM mysql.user WHERE User = '' AND Host = 'localhost';
FLUSH PRIVILEGES;
SQL

log "Installing Redis 7"
mkdir -p "$APP_DIR/redis" "$APP_DIR/redis/data" /tmp/library-redis-src
curl -L --retry 3 --fail -o /tmp/library-redis.tar.gz "$REDIS_URL"
tar -xzf /tmp/library-redis.tar.gz -C /tmp/library-redis-src --strip-components=1
make -C /tmp/library-redis-src -j2 MALLOC=libc BUILD_TLS=no
make -C /tmp/library-redis-src PREFIX="$APP_DIR/redis" install
id redis >/dev/null 2>&1 || useradd -r -s /sbin/nologin redis
chown -R redis:redis "$APP_DIR/redis/data"

log "Configuring Redis"
sed "s/__REDIS_PASSWORD__/${REDIS_PASSWORD}/g" \
    /tmp/library-deploy/conf/redis-library.conf > "$APP_DIR/redis/redis.conf"
sysctl -w vm.overcommit_memory=1 >/dev/null
sysctl -w net.core.somaxconn=1024 >/dev/null
if ! grep -q '^vm.overcommit_memory' /etc/sysctl.conf; then
    printf '\nvm.overcommit_memory = 1\n' >> /etc/sysctl.conf
fi
if ! grep -q '^net.core.somaxconn' /etc/sysctl.conf; then
    printf 'net.core.somaxconn = 1024\n' >> /etc/sysctl.conf
fi
install -m 0644 /tmp/library-deploy/systemd/redis-library.service /etc/systemd/system/redis-library.service
systemctl daemon-reload
systemctl enable redis-library
systemctl restart redis-library
wait_for_redis

log "Installing application files"
id library >/dev/null 2>&1 || useradd -r -s /sbin/nologin library
mkdir -p "$APP_DIR/app" "$APP_DIR/config" "$APP_DIR/logs"
install -m 0644 /root/library-server.jar "$APP_DIR/app/library-server.jar"
chown -R library:library "$APP_DIR/app" "$APP_DIR/logs"
chown root:root "$APP_DIR/config"

cat > "$APP_DIR/config/library.env" <<ENV
SERVER_PORT=18080
SERVER_ADDRESS=127.0.0.1
DB_URL=jdbc:mysql://127.0.0.1:3306/library_db?useUnicode=true&characterEncoding=utf8&serverTimezone=Asia/Shanghai&useSSL=false&allowPublicKeyRetrieval=true
DB_USERNAME=library_app
DB_PASSWORD=${MYSQL_APP_PASSWORD}
REDIS_HOST=127.0.0.1
REDIS_PORT=6379
REDIS_PASSWORD=${REDIS_PASSWORD}
REDIS_DATABASE=0
TOKEN_TTL=604800
SQL_INIT_MODE=always
JAVA_OPTS=-Xms128m -Xmx384m -XX:MaxMetaspaceSize=192m -XX:+UseG1GC -Dfile.encoding=UTF-8
ENV
chmod 600 "$APP_DIR/config/library.env"

log "Configuring Nginx"
install -m 0644 /tmp/library-deploy/nginx/library.conf /etc/nginx/conf.d/library.conf
if ! grep -q 'listen.*80.*default_server' /etc/nginx/nginx.conf; then
    cp -a /etc/nginx/nginx.conf "/etc/nginx/nginx.conf.library-backup-$(date +%Y%m%d%H%M%S)"
    sed -i '0,/listen[[:space:]]*80;/s//listen 80 default_server;/' /etc/nginx/nginx.conf
    sed -i '0,/listen[[:space:]]*\[::\]:80;/s//listen [::]:80 default_server;/' /etc/nginx/nginx.conf
fi
systemctl enable nginx
nginx -t
systemctl reload nginx

log "Starting application for first-time schema initialization"
install -m 0644 /tmp/library-deploy/systemd/library-server.service /etc/systemd/system/library-server.service
systemctl daemon-reload
systemctl enable library-server
systemctl restart library-server
wait_for_app

log "Updating default accounts"
mysql -uroot -p"${MYSQL_ROOT_PASSWORD}" library_db <<SQL
UPDATE sys_user SET password = '${ADMIN_PASSWORD_HASH}' WHERE username = 'admin';
UPDATE sys_user SET password = '${READER_PASSWORD_HASH}' WHERE username IN ('reader', 'reader2', 'reader3', 'reader4', 'reader5');
SQL

log "Switching schema initialization off and restarting application"
sed -i 's/^SQL_INIT_MODE=.*/SQL_INIT_MODE=never/' "$APP_DIR/config/library.env"
systemctl restart library-server
wait_for_app

log "Writing credentials file"
cat > /root/library-credentials.txt <<EOF
Library system production credentials
Generated: $(date '+%Y-%m-%d %H:%M:%S %Z')

Admin URL: https://lxlssr.icu
Admin username: admin
Admin password: ${ADMIN_PASSWORD}
Reader usernames: reader, reader2, reader3, reader4, reader5
Reader password: ${READER_PASSWORD}

MySQL root password: ${MYSQL_ROOT_PASSWORD}
MySQL application username: library_app
MySQL application password: ${MYSQL_APP_PASSWORD}
Redis password: ${REDIS_PASSWORD}

Keep this file private. Rotate all values after any suspected exposure.
EOF
chmod 600 /root/library-credentials.txt

log "Deployment completed"
systemctl is-active mysqld redis-library library-server nginx
printf 'Java: %s\n' "$("$APP_DIR/java/bin/java" -version 2>&1 | head -1)"
printf 'MySQL: %s\n' "$(mysql --version)"
printf 'Redis: %s\n' "$("$APP_DIR/redis/bin/redis-server" --version | head -1)"
printf 'App: %s\n' "$(curl -fsS http://127.0.0.1:18080/ | head -c 80)"
