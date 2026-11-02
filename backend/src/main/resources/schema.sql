CREATE TABLE IF NOT EXISTS sys_user (
    id BIGINT NOT NULL AUTO_INCREMENT,
    username VARCHAR(50) NOT NULL,
    password VARCHAR(100) NOT NULL,
    real_name VARCHAR(50) DEFAULT NULL,
    role VARCHAR(20) NOT NULL DEFAULT 'READER',
    status TINYINT NOT NULL DEFAULT 1,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    deleted TINYINT NOT NULL DEFAULT 0,
    PRIMARY KEY (id),
    UNIQUE KEY uk_user_username (username),
    KEY idx_user_role_status (role, status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS book (
    id BIGINT NOT NULL AUTO_INCREMENT,
    isbn VARCHAR(32) NOT NULL,
    title VARCHAR(150) NOT NULL,
    author VARCHAR(100) NOT NULL,
    publisher VARCHAR(100) DEFAULT NULL,
    category VARCHAR(50) DEFAULT NULL,
    price DECIMAL(10, 2) NOT NULL DEFAULT 0.00,
    total_stock INT NOT NULL DEFAULT 0,
    available_stock INT NOT NULL DEFAULT 0,
    description VARCHAR(1000) DEFAULT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    deleted TINYINT NOT NULL DEFAULT 0,
    PRIMARY KEY (id),
    UNIQUE KEY uk_book_isbn (isbn),
    KEY idx_book_title (title),
    KEY idx_book_category (category)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS borrow_record (
    id BIGINT NOT NULL AUTO_INCREMENT,
    borrow_no VARCHAR(40) NOT NULL,
    user_id BIGINT NOT NULL,
    book_id BIGINT NOT NULL,
    book_title VARCHAR(150) NOT NULL,
    book_isbn VARCHAR(32) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'BORROWED',
    borrowed_at DATETIME NOT NULL,
    due_at DATETIME NOT NULL,
    returned_at DATETIME DEFAULT NULL,
    renew_count INT NOT NULL DEFAULT 0,
    fine_amount DECIMAL(10, 2) NOT NULL DEFAULT 0.00,
    fine_status VARCHAR(20) NOT NULL DEFAULT 'NONE',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    deleted TINYINT NOT NULL DEFAULT 0,
    PRIMARY KEY (id),
    UNIQUE KEY uk_borrow_no (borrow_no),
    KEY idx_borrow_user_status (user_id, status),
    KEY idx_borrow_book_status (book_id, status),
    KEY idx_borrow_created (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

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
