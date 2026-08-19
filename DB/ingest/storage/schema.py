"""Database schema definitions and initialization."""

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS vendors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    display_name TEXT NOT NULL,
    website TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_url TEXT NOT NULL,
    source_type TEXT NOT NULL,
    vendor_id INTEGER REFERENCES vendors(id),
    retrieved_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    http_status INTEGER DEFAULT 200,
    content_hash TEXT,
    etag TEXT,
    last_modified TEXT,
    UNIQUE(vendor_id, source_url)
);
CREATE INDEX IF NOT EXISTS idx_sources_url ON sources(source_url);

CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vendor_id INTEGER NOT NULL REFERENCES vendors(id),
    identity_key TEXT NOT NULL,
    canonical_name TEXT NOT NULL,
    raw_name TEXT NOT NULL,
    category TEXT NOT NULL,
    category_confidence REAL DEFAULT 0.5,
    metadata_confidence REAL DEFAULT 0.5,
    product_url TEXT,
    image_url TEXT,
    first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    active INTEGER DEFAULT 1,
    UNIQUE(vendor_id, identity_key)
);
CREATE INDEX IF NOT EXISTS idx_products_vendor ON products(vendor_id);
CREATE INDEX IF NOT EXISTS idx_products_name ON products(canonical_name);
CREATE INDEX IF NOT EXISTS idx_products_identity ON products(identity_key);

CREATE TABLE IF NOT EXISTS product_aliases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL REFERENCES products(id),
    alias_name TEXT NOT NULL,
    alias_url TEXT,
    source_id INTEGER REFERENCES sources(id),
    evidence_level INTEGER NOT NULL,
    first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(product_id, alias_name)
);
CREATE INDEX IF NOT EXISTS idx_aliases_prod ON product_aliases(product_id);

CREATE TABLE IF NOT EXISTS artifacts (
    sha256 TEXT PRIMARY KEY,
    filename TEXT NOT NULL,
    size INTEGER NOT NULL,
    original_url TEXT NOT NULL,
    final_url TEXT,
    content_type TEXT,
    etag TEXT,
    last_modified TEXT,
    normalized_url TEXT,
    downloaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    software_version TEXT,
    extraction_status TEXT DEFAULT 'pending',
    vendor_id INTEGER REFERENCES vendors(id),
    first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_artifacts_orig_url ON artifacts(original_url);
CREATE INDEX IF NOT EXISTS idx_artifacts_norm_url ON artifacts(normalized_url);

CREATE TABLE IF NOT EXISTS artifact_urls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    normalized_url TEXT UNIQUE NOT NULL,
    original_url TEXT NOT NULL,
    final_url TEXT,
    etag TEXT,
    last_modified TEXT,
    sha256 TEXT REFERENCES artifacts(sha256),
    vendor_id INTEGER REFERENCES vendors(id),
    size INTEGER,
    status TEXT DEFAULT 'downloaded',
    first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_artifact_urls_norm ON artifact_urls(normalized_url);
CREATE INDEX IF NOT EXISTS idx_artifact_urls_sha ON artifact_urls(sha256);

CREATE TABLE IF NOT EXISTS product_artifacts (
    product_id INTEGER REFERENCES products(id),
    artifact_sha256 TEXT REFERENCES artifacts(sha256),
    relation_type TEXT DEFAULT 'driver',
    PRIMARY KEY (product_id, artifact_sha256)
);

CREATE TABLE IF NOT EXISTS device_identifiers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER REFERENCES products(id),
    vid INTEGER NOT NULL,
    pid INTEGER NOT NULL,
    vid_hex TEXT NOT NULL,
    pid_hex TEXT NOT NULL,
    manufacturer_string TEXT,
    product_string TEXT,
    usage_page INTEGER,
    usage INTEGER,
    connection_type TEXT,
    source_id INTEGER REFERENCES sources(id),
    artifact_sha256 TEXT REFERENCES artifacts(sha256),
    evidence_level INTEGER NOT NULL,
    confidence REAL DEFAULT 1.0,
    first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(product_id, vid, pid, usage_page, usage, connection_type)
);
CREATE INDEX IF NOT EXISTS idx_devid_vid_pid ON device_identifiers(vid, pid);

CREATE TABLE IF NOT EXISTS protocol_hints (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER REFERENCES products(id),
    hint_key TEXT NOT NULL,
    hint_value TEXT NOT NULL,
    source_id INTEGER REFERENCES sources(id),
    artifact_sha256 TEXT REFERENCES artifacts(sha256),
    evidence_level INTEGER NOT NULL,
    confidence REAL DEFAULT 0.8,
    context TEXT,
    first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(product_id, hint_key, hint_value)
);

CREATE TABLE IF NOT EXISTS facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER REFERENCES products(id),
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    source_id INTEGER REFERENCES sources(id),
    artifact_sha256 TEXT REFERENCES artifacts(sha256),
    evidence_level INTEGER NOT NULL,
    confidence REAL DEFAULT 1.0,
    is_inference INTEGER DEFAULT 0,
    first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(product_id, key, value)
);

CREATE TABLE IF NOT EXISTS crawl_runs (
    id TEXT PRIMARY KEY,
    started_at TIMESTAMP NOT NULL,
    finished_at TIMESTAMP,
    status TEXT DEFAULT 'running',
    products_scanned INTEGER DEFAULT 0,
    new_products INTEGER DEFAULT 0,
    updated_products INTEGER DEFAULT 0,
    new_artifacts INTEGER DEFAULT 0,
    changed_artifacts INTEGER DEFAULT 0,
    new_vid_pids INTEGER DEFAULT 0,
    new_hints INTEGER DEFAULT 0,
    errors_count INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT REFERENCES crawl_runs(id),
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    change_type TEXT NOT NULL,
    details_json TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS possible_duplicates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id_a INTEGER REFERENCES products(id),
    product_id_b INTEGER REFERENCES products(id),
    reason TEXT NOT NULL,
    confidence REAL DEFAULT 0.5,
    reviewed INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(product_id_a, product_id_b)
);

CREATE TABLE IF NOT EXISTS brands (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT UNIQUE NOT NULL,
    canonical_name TEXT NOT NULL,
    brand_type TEXT DEFAULT 'brand',
    parent_brand_id INTEGER REFERENCES brands(id),
    company_group_id INTEGER REFERENCES brands(id),
    website TEXT,
    active INTEGER DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_brands_slug ON brands(slug);

CREATE TABLE IF NOT EXISTS brand_aliases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    brand_id INTEGER NOT NULL REFERENCES brands(id),
    alias TEXT NOT NULL,
    language_or_region TEXT,
    provenance TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(brand_id, alias)
);
CREATE INDEX IF NOT EXISTS idx_brand_aliases_brand ON brand_aliases(brand_id);

CREATE TABLE IF NOT EXISTS brand_relationships (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_brand_id INTEGER NOT NULL REFERENCES brands(id),
    target_brand_id INTEGER NOT NULL REFERENCES brands(id),
    relationship_type TEXT NOT NULL,
    confidence REAL DEFAULT 1.0,
    provenance TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source_brand_id, target_brand_id, relationship_type)
);
CREATE INDEX IF NOT EXISTS idx_brand_rel_source ON brand_relationships(source_brand_id);

CREATE TABLE IF NOT EXISTS brand_crawl_status (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    brand_id INTEGER NOT NULL REFERENCES brands(id),
    run_id TEXT,
    status TEXT NOT NULL,
    products_count INTEGER DEFAULT 0,
    devices_count INTEGER DEFAULT 0,
    artifacts_count INTEGER DEFAULT 0,
    artifacts_bytes INTEGER DEFAULT 0,
    vid_pids_count INTEGER DEFAULT 0,
    hints_count INTEGER DEFAULT 0,
    tech_evidence_products INTEGER DEFAULT 0,
    blocking_reason TEXT,
    crawled_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_brand_status_brand ON brand_crawl_status(brand_id);
"""
