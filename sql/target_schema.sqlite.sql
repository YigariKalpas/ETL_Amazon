-- 目标层建表（SQLite 版，用于本地落盘与校验）
-- 与 sql/target_schema.pg.sql 结构一致，差异只在类型与自增写法。
-- 设计依据：作业 §八「建议逻辑模型」。

PRAGMA foreign_keys = ON;

-- 批次与运行
CREATE TABLE IF NOT EXISTS etl_run (
    run_id        TEXT PRIMARY KEY,
    tool          TEXT NOT NULL,          -- python / pdi
    rule_version  TEXT NOT NULL,
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    status        TEXT NOT NULL,          -- running / ok / failed
    note          TEXT
);

-- 源记录：网页快照（原始层的锚点）
CREATE TABLE IF NOT EXISTS source_record (
    source_record_id TEXT PRIMARY KEY,    -- asin
    asin             TEXT NOT NULL,
    source_url       TEXT,
    snapshot_path    TEXT,
    content_sha256   TEXT,
    content_length   INTEGER,
    http_status      INTEGER,
    fetched_at       TEXT,
    status           TEXT NOT NULL,       -- success / not_found / parse_failed / validation_page
    note             TEXT
);
CREATE INDEX IF NOT EXISTS ix_source_status ON source_record(status);

-- 评论源文件的分片（原始层：数据文件版本与记录序号）
CREATE TABLE IF NOT EXISTS source_file (
    file_name   TEXT PRIMARY KEY,
    rows        INTEGER,
    gz_bytes    INTEGER,
    sha256      TEXT,
    rule_version TEXT
);

-- 作品
CREATE TABLE IF NOT EXISTS movie (
    movie_id        TEXT PRIMARY KEY,
    canonical_title TEXT NOT NULL,
    norm_title      TEXT,
    work_type       TEXT NOT NULL,        -- film / series / concert / anime / boxset
    release_year    INTEGER,              -- 缺则为空，不做推断
    status          TEXT NOT NULL,        -- single / auto / review
    rule_version    TEXT NOT NULL,
    edition_count   INTEGER NOT NULL DEFAULT 1
);

-- 商品版本（edition）：一个 ASIN 一条
CREATE TABLE IF NOT EXISTS product_edition (
    product_id            TEXT PRIMARY KEY,   -- ASIN
    movie_id              TEXT NOT NULL,
    media_format          TEXT,
    language              TEXT,
    discs                 TEXT,
    studio                TEXT,
    product_release_date  TEXT,               -- 商品发售日，不是上映日
    snapshot_path         TEXT,
    classification        TEXT,               -- 任务3 的分类标签
    breadcrumb_top        TEXT,
    FOREIGN KEY (movie_id) REFERENCES movie(movie_id)
);
CREATE INDEX IF NOT EXISTS ix_edition_movie ON product_edition(movie_id);

-- 商品 ↔ 作品（支持合集多对多）
CREATE TABLE IF NOT EXISTS product_movie (
    product_id    TEXT NOT NULL,
    movie_id      TEXT NOT NULL,
    relation_type TEXT NOT NULL,          -- single / same_work_edition / series_volume / boxset_member
    rule_version  TEXT NOT NULL,
    status        TEXT NOT NULL,          -- single / auto / review
    PRIMARY KEY (product_id, movie_id)
);

-- 合并判定（含未合并的证据）
CREATE TABLE IF NOT EXISTS merge_decision (
    decision_id   TEXT PRIMARY KEY,
    asin_a        TEXT NOT NULL,
    asin_b        TEXT NOT NULL,
    title_ratio   REAL,
    same_norm     INTEGER,
    director_match INTEGER,
    director_conflict INTEGER,
    cast_jaccard  REAL,
    runtime_delta INTEGER,
    year_a        INTEGER,
    year_b        INTEGER,
    year_conflict INTEGER,
    decision      TEXT NOT NULL,          -- auto / review / reject
    rule_id       TEXT NOT NULL,
    confidence    REAL,
    rule_version  TEXT NOT NULL
);

-- 合集成员候选（未确认前不建实体）
CREATE TABLE IF NOT EXISTS boxset_member_candidate (
    product_id       TEXT NOT NULL,
    member_index     INTEGER NOT NULL,
    member_title_raw TEXT NOT NULL,
    status           TEXT NOT NULL,       -- pending_review / confirmed / rejected
    rule_version     TEXT NOT NULL,
    PRIMARY KEY (product_id, member_index)
);

-- 评论者与评论（任务1 产物中与已采页面相关的部分）
CREATE TABLE IF NOT EXISTS reviewer (
    reviewer_id  TEXT PRIMARY KEY,
    profile_name TEXT
);

CREATE TABLE IF NOT EXISTS review (
    review_id         TEXT PRIMARY KEY,
    product_id        TEXT NOT NULL,
    reviewer_id       TEXT,
    score             REAL,
    review_time_epoch INTEGER,
    review_time_iso   TEXT,
    helpful_num       INTEGER,
    helpful_den       INTEGER,
    summary           TEXT,
    text              TEXT,
    summary_len       INTEGER,
    text_len          INTEGER,
    record_index      INTEGER,
    part_file         TEXT
);
CREATE INDEX IF NOT EXISTS ix_review_product ON review(product_id);
CREATE INDEX IF NOT EXISTS ix_review_time ON review(review_time_epoch);

-- 逻辑评论映射（任务8 占位）
CREATE TABLE IF NOT EXISTS review_mapping (
    review_id        TEXT PRIMARY KEY,
    logical_review_id TEXT NOT NULL,
    reason           TEXT
);

-- 人物（任务5 占位）
CREATE TABLE IF NOT EXISTS person (
    person_id     TEXT PRIMARY KEY,
    canonical_name TEXT,
    status        TEXT,
    rule_version  TEXT
);
CREATE TABLE IF NOT EXISTS person_alias (
    person_id   TEXT NOT NULL,
    alias_raw   TEXT NOT NULL,
    source      TEXT,
    PRIMARY KEY (person_id, alias_raw)
);
CREATE TABLE IF NOT EXISTS movie_person (
    movie_id  TEXT NOT NULL,
    person_id TEXT NOT NULL,
    role      TEXT NOT NULL,              -- director / actor
    billing_order INTEGER,
    source    TEXT,
    PRIMARY KEY (movie_id, person_id, role)
);

-- 字段级血缘（任务9 占位）
CREATE TABLE IF NOT EXISTS field_assertion (
    assertion_id     TEXT PRIMARY KEY,
    entity_type      TEXT NOT NULL,       -- movie / product / person
    entity_id        TEXT NOT NULL,
    field_name       TEXT NOT NULL,
    raw_value        TEXT,
    normalized_value TEXT,
    source_record_id TEXT,
    locator          TEXT,                -- 快照哈希#偏移 或 分片#record_index
    rule_id          TEXT,
    run_id           TEXT,
    confidence       REAL,
    created_at       TEXT
);

-- 字段选择（任务7）与上映信息（任务6）
CREATE TABLE IF NOT EXISTS field_selection (
    entity_type          TEXT NOT NULL,
    entity_id            TEXT NOT NULL,
    field_name           TEXT NOT NULL,
    selected_assertion_id TEXT,
    candidates           INTEGER,
    reason               TEXT,
    rule_version         TEXT,
    status               TEXT NOT NULL,   -- selected / inferred_only / missing / conflict
    PRIMARY KEY (entity_type, entity_id, field_name)
);

CREATE TABLE IF NOT EXISTS release_date (
    movie_id     TEXT PRIMARY KEY,
    value_date   TEXT,
    value_year   INTEGER,
    precision    TEXT,
    status       TEXT NOT NULL,           -- verified / inferred / missing
    source       TEXT,
    method       TEXT,
    condition    TEXT,
    confidence   REAL,
    retrieved_at TEXT
);

-- 质量与异常
CREATE TABLE IF NOT EXISTS quality_issue (
    issue_id    TEXT PRIMARY KEY,
    entity_type TEXT,
    entity_id   TEXT,
    issue_type  TEXT NOT NULL,
    detail      TEXT,
    source      TEXT,
    run_id      TEXT
);

-- 分类结果（任务3 的落盘副本）
CREATE TABLE IF NOT EXISTS product_classification (
    product_id      TEXT PRIMARY KEY,
    classification  TEXT NOT NULL,
    labels          TEXT,
    review_required INTEGER,
    rule_id         TEXT,
    strength        TEXT,
    collection_status TEXT
);
