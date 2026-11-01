-- 已有数据库升级脚本：借阅规则中心、预约排队、操作日志
-- 新环境不需要执行（schema.sql 已经是升级后的结构），老库执行一次即可。

ALTER TABLE borrow_record
    ADD COLUMN renew_count INT NOT NULL DEFAULT 0 AFTER returned_at,
    ADD COLUMN fine_amount DECIMAL(10, 2) NOT NULL DEFAULT 0.00 AFTER renew_count,
    ADD COLUMN fine_status VARCHAR(20) NOT NULL DEFAULT 'NONE' AFTER fine_amount,
    ADD KEY idx_borrow_created (created_at);

CREATE TABLE IF NOT EXISTS borrow_rule (
    id BIGINT NOT NULL AUTO_INCREMENT,
    max_borrow_count INT NOT NULL DEFAULT 5,
    borrow_days INT NOT NULL DEFAULT 30,
    max_renew_count INT NOT NULL DEFAULT 1,
    renew_days INT NOT NULL DEFAULT 15,
    fine_per_day DECIMAL(10, 2) NOT NULL DEFAULT 0.20,
    fine_max DECIMAL(10, 2) NOT NULL DEFAULT 20.00,
    reserve_limit INT NOT NULL DEFAULT 3,
    hold_days INT NOT NULL DEFAULT 3,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS book_reservation (
    id BIGINT NOT NULL AUTO_INCREMENT,
    book_id BIGINT NOT NULL,
    user_id BIGINT NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'WAITING',
    ready_at DATETIME DEFAULT NULL,
    expire_at DATETIME DEFAULT NULL,
    completed_at DATETIME DEFAULT NULL,
    canceled_at DATETIME DEFAULT NULL,
    active_key VARCHAR(64) GENERATED ALWAYS AS (
        CASE WHEN status IN ('WAITING', 'READY') THEN CONCAT(user_id, '-', book_id) ELSE NULL END
    ) STORED,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    deleted TINYINT NOT NULL DEFAULT 0,
    PRIMARY KEY (id),
    UNIQUE KEY uk_reservation_active (active_key),
    KEY idx_reservation_book_status (book_id, status, created_at),
    KEY idx_reservation_user_status (user_id, status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS operation_log (
    id BIGINT NOT NULL AUTO_INCREMENT,
    user_id BIGINT DEFAULT NULL,
    username VARCHAR(50) DEFAULT NULL,
    module VARCHAR(50) NOT NULL,
    action VARCHAR(100) NOT NULL,
    method VARCHAR(10) DEFAULT NULL,
    uri VARCHAR(200) DEFAULT NULL,
    params VARCHAR(1000) DEFAULT NULL,
    ip VARCHAR(64) DEFAULT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'SUCCESS',
    error_message VARCHAR(500) DEFAULT NULL,
    duration_ms BIGINT NOT NULL DEFAULT 0,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_log_module_created (module, created_at),
    KEY idx_log_user_created (user_id, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT INTO borrow_rule (id, max_borrow_count, borrow_days, max_renew_count, renew_days, fine_per_day, fine_max, reserve_limit, hold_days)
VALUES (1, 5, 30, 1, 15, 0.20, 20.00, 3, 3)
ON DUPLICATE KEY UPDATE id = id;
