-- 目标层建表（PostgreSQL 17 版；与 sqlite 版结构一致）
-- 用法：psql -f sql/target_schema.pg.sql（服务由用户自行启动）

BEGIN;

CREATE TABLE IF NOT EXISTS etl_run (
    run_id        TEXT PRIMARY KEY,
    tool          TEXT NOT NULL,
    rule_version  TEXT NOT NULL,
    started_at    TIMESTAMPTZ NOT NULL,
    finished_at   TIMESTAMPTZ,
    status        TEXT NOT NULL,
    note          TEXT
);

CREATE TABLE IF NOT EXISTS source_record (
    source_record_id TEXT PRIMARY KEY,
    asin             TEXT NOT NULL,
    source_url       TEXT,
    snapshot_path    TEXT,
    content_sha256   TEXT,
    content_length   BIGINT,
    http_status      INTEGER,
    fetched_at       TIMESTAMPTZ,
    status           TEXT NOT NULL,
    note             TEXT
);
CREATE INDEX IF NOT EXISTS ix_source_status ON source_record(status);

CREATE TABLE IF NOT EXISTS source_file (
    file_name    TEXT PRIMARY KEY,
    rows         INTEGER,
    gz_bytes     BIGINT,
    sha256       TEXT,
    rule_version TEXT
);

CREATE TABLE IF NOT EXISTS movie (
    movie_id        TEXT PRIMARY KEY,
    canonical_title TEXT NOT NULL,
    norm_title      TEXT,
    work_type       TEXT NOT NULL,
    release_year    INTEGER,
    status          TEXT NOT NULL,
    rule_version    TEXT NOT NULL,
    edition_count   INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS product_edition (
    product_id            TEXT PRIMARY KEY,
    movie_id              TEXT NOT NULL REFERENCES movie(movie_id),
    media_format          TEXT,
    language              TEXT,
    discs                 TEXT,
    studio                TEXT,
    product_release_date  TEXT,
    snapshot_path         TEXT,
    classification        TEXT,
    breadcrumb_top        TEXT
);
CREATE INDEX IF NOT EXISTS ix_edition_movie ON product_edition(movie_id);

CREATE TABLE IF NOT EXISTS product_movie (
    product_id    TEXT NOT NULL,
    movie_id      TEXT NOT NULL REFERENCES movie(movie_id),
    relation_type TEXT NOT NULL,
    rule_version  TEXT NOT NULL,
    status        TEXT NOT NULL,
    PRIMARY KEY (product_id, movie_id)
);

CREATE TABLE IF NOT EXISTS merge_decision (
    decision_id   TEXT PRIMARY KEY,
    asin_a        TEXT NOT NULL,
    asin_b        TEXT NOT NULL,
    title_ratio   NUMERIC(5,3),
    same_norm     BOOLEAN,
    director_match BOOLEAN,
    director_conflict BOOLEAN,
    cast_jaccard  NUMERIC(5,3),
    runtime_delta INTEGER,
    year_a        INTEGER,
    year_b        INTEGER,
    year_conflict BOOLEAN,
    decision      TEXT NOT NULL,
    rule_id       TEXT NOT NULL,
    confidence    NUMERIC(4,2),
    rule_version  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS boxset_member_candidate (
    product_id       TEXT NOT NULL,
    member_index     INTEGER NOT NULL,
    member_title_raw TEXT NOT NULL,
    status           TEXT NOT NULL,
    rule_version     TEXT NOT NULL,
    PRIMARY KEY (product_id, member_index)
);

CREATE TABLE IF NOT EXISTS reviewer (
    reviewer_id  TEXT PRIMARY KEY,
    profile_name TEXT
);

CREATE TABLE IF NOT EXISTS review (
    review_id         TEXT PRIMARY KEY,
    product_id        TEXT NOT NULL,
    reviewer_id       TEXT,
    score             NUMERIC(3,1),
    review_time_epoch BIGINT,
    review_time_iso   TIMESTAMPTZ,
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

CREATE TABLE IF NOT EXISTS review_mapping (
    review_id         TEXT PRIMARY KEY,
    logical_review_id TEXT NOT NULL,
    reason            TEXT
);

CREATE TABLE IF NOT EXISTS person (
    person_id      TEXT PRIMARY KEY,
    canonical_name TEXT,
    status         TEXT,
    rule_version   TEXT
);
CREATE TABLE IF NOT EXISTS person_alias (
    person_id TEXT NOT NULL,
    alias_raw TEXT NOT NULL,
    source    TEXT,
    PRIMARY KEY (person_id, alias_raw)
);
CREATE TABLE IF NOT EXISTS movie_person (
    movie_id      TEXT NOT NULL,
    person_id     TEXT NOT NULL,
    role          TEXT NOT NULL,
    billing_order INTEGER,
    source        TEXT,
    PRIMARY KEY (movie_id, person_id, role)
);

CREATE TABLE IF NOT EXISTS field_assertion (
    assertion_id     TEXT PRIMARY KEY,
    entity_type      TEXT NOT NULL,
    entity_id        TEXT NOT NULL,
    field_name       TEXT NOT NULL,
    raw_value        TEXT,
    normalized_value TEXT,
    source_record_id TEXT,
    locator          TEXT,
    rule_id          TEXT,
    run_id           TEXT,
    confidence       NUMERIC(4,2),
    created_at       TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS field_selection (
    entity_type           TEXT NOT NULL,
    entity_id             TEXT NOT NULL,
    field_name            TEXT NOT NULL,
    selected_assertion_id TEXT,
    candidates            INTEGER,
    reason                TEXT,
    rule_version          TEXT,
    status                TEXT NOT NULL,
    PRIMARY KEY (entity_type, entity_id, field_name)
);

CREATE TABLE IF NOT EXISTS release_date (
    movie_id     TEXT PRIMARY KEY,
    value_date   TEXT,
    value_year   INTEGER,
    precision    TEXT,
    status       TEXT NOT NULL,
    source       TEXT,
    method       TEXT,
    condition    TEXT,
    confidence   NUMERIC(4,2),
    retrieved_at TEXT
);

CREATE TABLE IF NOT EXISTS quality_issue (
    issue_id    TEXT PRIMARY KEY,
    entity_type TEXT,
    entity_id   TEXT,
    issue_type  TEXT NOT NULL,
    detail      TEXT,
    source      TEXT,
    run_id      TEXT
);

CREATE TABLE IF NOT EXISTS product_classification (
    product_id        TEXT PRIMARY KEY,
    classification    TEXT NOT NULL,
    labels            TEXT,
    review_required   BOOLEAN,
    rule_id           TEXT,
    strength          TEXT,
    collection_status TEXT
);

COMMIT;
