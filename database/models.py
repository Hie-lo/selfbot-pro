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

-- ── پاکسازی بنرهای تکراری ──
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'uniq_banner') THEN
        DELETE FROM banners a USING banners b
        WHERE a.id > b.id
          AND a.user_id = b.user_id
          AND a.chat_id = b.chat_id
          AND a.source_msg_id = b.source_msg_id;
    END IF;
END $$;

-- بدون این index، ON CONFLICT DO NOTHING در save_banner()
-- هیچ کاری نمی‌کند و بنر تکراری ساخته می‌شود
CREATE UNIQUE INDEX IF NOT EXISTS uniq_banner
    ON banners(user_id, chat_id, source_msg_id);


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

-- بدون این index، ON CONFLICT DO NOTHING در
-- save_auto_response_rule() بی‌اثر است
CREATE UNIQUE INDEX IF NOT EXISTS uniq_auto_response
    ON auto_response_rules(user_id, target_user_id);


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

-- ── پاکسازی رکوردهای تکراری (فقط یک‌بار، روی دیتابیس‌های قدیمی) ──
-- بدون این کار ساخت unique index شکست می‌خورد
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_indexes WHERE indexname = 'uniq_saved_msg'
    ) THEN
        DELETE FROM saved_messages a USING saved_messages b
        WHERE a.id > b.id
          AND a.user_id = b.user_id
          AND a.source_type = b.source_type
          AND a.source_chat_id IS NOT DISTINCT FROM b.source_chat_id
          AND a.source_msg_id IS NOT DISTINCT FROM b.source_msg_id;
    END IF;
END $$;

-- ── حیاتی: بدون این unique index، ON CONFLICT در
-- db.save_message_record() با خطای 42P10 شکست می‌خورد ──
CREATE UNIQUE INDEX IF NOT EXISTS uniq_saved_msg
    ON saved_messages(user_id, source_type, source_chat_id, source_msg_id);


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


CREATE TABLE IF NOT EXISTS subscription_requests (
    id              SERIAL PRIMARY KEY,
    user_id         INT REFERENCES users(id) ON DELETE CASCADE,
    plan_key        VARCHAR(20) NOT NULL,
    plan_title      VARCHAR(50) DEFAULT '',
    price           BIGINT DEFAULT 0,
    days            INT DEFAULT 0,
    status          VARCHAR(20) DEFAULT 'pending',
    receipt_chat_id BIGINT,
    receipt_msg_id  BIGINT,
    receipt_file_id TEXT,
    admin_msg_id    BIGINT,
    reviewed_by     BIGINT,
    reject_reason   TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    reviewed_at     TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_subreq_user
    ON subscription_requests(user_id);
CREATE INDEX IF NOT EXISTS idx_subreq_status
    ON subscription_requests(status);
CREATE INDEX IF NOT EXISTS idx_subreq_created
    ON subscription_requests(created_at);

-- فقط یک درخواست در انتظار بررسی برای هر کاربر
CREATE UNIQUE INDEX IF NOT EXISTS uniq_subreq_pending
    ON subscription_requests(user_id)
    WHERE status = 'pending';


CREATE TABLE IF NOT EXISTS forward_jobs (
    id              SERIAL PRIMARY KEY,
    user_id         INT REFERENCES users(id) ON DELETE CASCADE,
    src_kind        VARCHAR(10),
    src_id          BIGINT,
    src_hash        BIGINT,
    src_name        VARCHAR(255) DEFAULT '',
    dst_kind        VARCHAR(10),
    dst_id          BIGINT,
    dst_hash        BIGINT,
    dst_name        VARCHAR(255) DEFAULT '',
    mode            VARCHAR(20) DEFAULT 'attributed',
    attributed      BOOLEAN DEFAULT TRUE,
    media_only      BOOLEAN DEFAULT FALSE,
    limit_count     INT DEFAULT 0,
    last_msg_id     BIGINT DEFAULT 0,
    sent            INT DEFAULT 0,
    skipped         INT DEFAULT 0,
    failed          INT DEFAULT 0,
    copied          INT DEFAULT 0,
    total           INT DEFAULT 0,
    status          VARCHAR(20) DEFAULT 'running',
    error           TEXT,
    chat_id         BIGINT,
    message_id      BIGINT,
    -- حالت دو مرحله‌ای: اول کش روی سرور، بعد ارسال
    cache_mode      BOOLEAN DEFAULT FALSE,
    capture_media   BOOLEAN DEFAULT FALSE,
    phase           VARCHAR(20) DEFAULT 'send',
    captured        INT DEFAULT 0,
    cache_bytes     BIGINT DEFAULT 0,
    capture_cursor  BIGINT DEFAULT 0,
    src_label       VARCHAR(255) DEFAULT '',
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- برای دیتابیس‌های موجود
ALTER TABLE forward_jobs ADD COLUMN IF NOT EXISTS cache_mode BOOLEAN DEFAULT FALSE;
ALTER TABLE forward_jobs ADD COLUMN IF NOT EXISTS capture_media BOOLEAN DEFAULT FALSE;
ALTER TABLE forward_jobs ADD COLUMN IF NOT EXISTS phase VARCHAR(20) DEFAULT 'send';
ALTER TABLE forward_jobs ADD COLUMN IF NOT EXISTS captured INT DEFAULT 0;
ALTER TABLE forward_jobs ADD COLUMN IF NOT EXISTS cache_bytes BIGINT DEFAULT 0;
ALTER TABLE forward_jobs ADD COLUMN IF NOT EXISTS capture_cursor BIGINT DEFAULT 0;
ALTER TABLE forward_jobs ADD COLUMN IF NOT EXISTS src_label VARCHAR(255) DEFAULT '';
ALTER TABLE forward_jobs ADD COLUMN IF NOT EXISTS speed VARCHAR(16) DEFAULT 'balanced';

CREATE INDEX IF NOT EXISTS idx_fwj_user
    ON forward_jobs(user_id);
CREATE INDEX IF NOT EXISTS idx_fwj_status
    ON forward_jobs(status);

-- فقط یک job در جریان برای هر کاربر
CREATE UNIQUE INDEX IF NOT EXISTS uniq_fwj_running
    ON forward_jobs(user_id)
    WHERE status IN ('running', 'paused');


-- ═══════ کش پیام‌ها (ذخیره موقت روی سرور) ═══════
-- همه پیام‌های مبدأ قبل از ارسال اینجا ذخیره می‌شوند تا حتی اگر
-- دسترسی به کانال مبدأ از دست برود، ارسال ادامه پیدا کند.
CREATE TABLE IF NOT EXISTS forward_cache (
    id              SERIAL PRIMARY KEY,
    job_id          INT REFERENCES forward_jobs(id) ON DELETE CASCADE,
    user_id         INT,
    src_msg_id      BIGINT NOT NULL,
    msg_date        TIMESTAMPTZ,
    text            TEXT,
    media_kind      VARCHAR(20) DEFAULT '',
    media_path      TEXT,
    media_size      BIGINT DEFAULT 0,
    doc_id          BIGINT,
    doc_hash        BIGINT,
    file_ref        BYTEA,
    sender_name     VARCHAR(255) DEFAULT '',
    sender_label    VARCHAR(100) DEFAULT '',
    msg_link        TEXT,
    sent_at         TIMESTAMPTZ,
    send_error      TEXT,
    cached_at       TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(job_id, src_msg_id)
);

CREATE INDEX IF NOT EXISTS idx_fwc_job
    ON forward_cache(job_id);
CREATE INDEX IF NOT EXISTS idx_fwc_pending
    ON forward_cache(job_id, src_msg_id) WHERE sent_at IS NULL;
"""
