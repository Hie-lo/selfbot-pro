"""
SQL جداول
"""

TABLES_SQL = """

CREATE TABLE IF NOT EXISTS users (
    id              SERIAL PRIMARY KEY,
    telegram_id     BIGINT UNIQUE NOT NULL,
    phone_enc       TEXT,
    phone_hash      VARCHAR(64),
    first_name      VARCHAR(255) DEFAULT '',
    username        VARCHAR(255) DEFAULT '',
    plan            VARCHAR(20) DEFAULT 'free',
    plan_expires_at TIMESTAMPTZ,
    is_active       BOOLEAN DEFAULT TRUE,
    is_banned       BOOLEAN DEFAULT FALSE,
    language        VARCHAR(5) DEFAULT 'fa',
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    last_seen_at    TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_users_tgid
    ON users(telegram_id);
CREATE INDEX IF NOT EXISTS idx_users_phone
    ON users(phone_hash);


CREATE TABLE IF NOT EXISTS account_sessions (
    id                  SERIAL PRIMARY KEY,
    user_id             INT REFERENCES users(id) ON DELETE CASCADE,
    phone_hash          VARCHAR(64) NOT NULL,
    session_data_enc    TEXT,
    api_id_enc          TEXT,
    api_hash_enc        TEXT,
    is_connected        BOOLEAN DEFAULT FALSE,
    last_connected_at   TIMESTAMPTZ,
    status              VARCHAR(20) DEFAULT 'inactive',
    error_message       TEXT,
    created_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_sess_user
    ON account_sessions(user_id);


CREATE TABLE IF NOT EXISTS feature_toggles (
    id              SERIAL PRIMARY KEY,
    user_id         INT REFERENCES users(id) ON DELETE CASCADE,
    feature_name    VARCHAR(50) NOT NULL,
    is_enabled      BOOLEAN DEFAULT FALSE,
    config_json     JSONB DEFAULT '{}',
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(user_id, feature_name)
);

CREATE INDEX IF NOT EXISTS idx_feat_user
    ON feature_toggles(user_id);


CREATE TABLE IF NOT EXISTS storage_targets (
    id              SERIAL PRIMARY KEY,
    user_id         INT REFERENCES users(id) ON DELETE CASCADE,
    feature_name    VARCHAR(50) NOT NULL,
    target_type     VARCHAR(20) NOT NULL,
    target_id       BIGINT,
    target_title    VARCHAR(255) DEFAULT '',
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(user_id, feature_name)
);

CREATE INDEX IF NOT EXISTS idx_stor_user
    ON storage_targets(user_id);


CREATE TABLE IF NOT EXISTS banners (
    id                  SERIAL PRIMARY KEY,
    user_id             INT REFERENCES users(id) ON DELETE CASCADE,
    chat_id             BIGINT NOT NULL,
    source_msg_id       BIGINT NOT NULL,
    interval_seconds    INT NOT NULL,
    is_active           BOOLEAN DEFAULT TRUE,
    created_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ban_user
    ON banners(user_id);


CREATE TABLE IF NOT EXISTS auto_response_rules (
    id                  SERIAL PRIMARY KEY,
    user_id             INT REFERENCES users(id) ON DELETE CASCADE,
    target_user_id      BIGINT,
    trigger_type        VARCHAR(20) DEFAULT 'any_message',
    trigger_value       TEXT,
    response_type       VARCHAR(20) DEFAULT 'random_from_list',
    response_list       JSONB DEFAULT '[]',
    is_active           BOOLEAN DEFAULT TRUE,
    cooldown_seconds    INT DEFAULT 5,
    created_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_rule_user
    ON auto_response_rules(user_id);


CREATE TABLE IF NOT EXISTS channel_monitors (
    id                      SERIAL PRIMARY KEY,
    user_id                 INT REFERENCES users(id) ON DELETE CASCADE,
    source_channel_id       BIGINT NOT NULL,
    source_channel_title    VARCHAR(255) DEFAULT '',
    destination_type        VARCHAR(20) NOT NULL,
    destination_id          BIGINT,
    destination_title       VARCHAR(255) DEFAULT '',
    filter_type             VARCHAR(20) DEFAULT 'all',
    is_active               BOOLEAN DEFAULT TRUE,
    created_at              TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_mon_user
    ON channel_monitors(user_id);


CREATE TABLE IF NOT EXISTS saved_messages (
    id                  SERIAL PRIMARY KEY,
    user_id             INT REFERENCES users(id) ON DELETE CASCADE,
    source_type         VARCHAR(20) NOT NULL,
    source_chat_id      BIGINT,
    source_chat_title   VARCHAR(255) DEFAULT '',
    source_msg_id       BIGINT,
    original_text       TEXT,
    edited_text         TEXT,
    media_type          VARCHAR(50),
    media_path_enc      TEXT,
    forwarded_to        BIGINT,
    timestamp           TIMESTAMPTZ,
    created_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_saved_user
    ON saved_messages(user_id);
CREATE INDEX IF NOT EXISTS idx_saved_type
    ON saved_messages(source_type);


CREATE TABLE IF NOT EXISTS audit_logs (
    id          SERIAL PRIMARY KEY,
    user_id     INT REFERENCES users(id) ON DELETE SET NULL,
    action      VARCHAR(50) NOT NULL,
    detail      TEXT DEFAULT '',
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_audit_user
    ON audit_logs(user_id);

CREATE TABLE IF NOT EXISTS channel_monitor_routes (
    id                      SERIAL PRIMARY KEY,
    user_id                 INT REFERENCES users(id) ON DELETE CASCADE,
    source_channel_id       BIGINT NOT NULL,
    source_channel_title    VARCHAR(255) DEFAULT '',
    destination_type        VARCHAR(20) NOT NULL,
    destination_id          BIGINT,
    destination_title       VARCHAR(255) DEFAULT '',
    filter_type             VARCHAR(20) DEFAULT 'all',
    is_active               BOOLEAN DEFAULT TRUE,
    created_at              TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(user_id, source_channel_id)
);

CREATE INDEX IF NOT EXISTS idx_cmr_user
    ON channel_monitor_routes(user_id);
"""