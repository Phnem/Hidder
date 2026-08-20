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

-- Forensic-audit extension.  The original facts table deliberately remains
-- backwards compatible; normalized_facts is the evidence-aware canonical layer.
CREATE TABLE IF NOT EXISTS audit_schema_versions (
    version INTEGER PRIMARY KEY,
    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS source_roots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    root_name TEXT NOT NULL UNIQUE,
    local_path TEXT NOT NULL,
    repository_url TEXT,
    commit_sha TEXT,
    branch TEXT,
    license_file TEXT,
    license_text TEXT,
    root_content_hash TEXT,
    source_kind TEXT NOT NULL DEFAULT 'repository',
    audit_status TEXT NOT NULL DEFAULT 'discovered',
    audited_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS source_files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_root_id INTEGER NOT NULL REFERENCES source_roots(id) ON DELETE CASCADE,
    relative_path TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    size INTEGER NOT NULL,
    relevant INTEGER NOT NULL DEFAULT 0 CHECK(relevant IN (0, 1)),
    parsed INTEGER NOT NULL DEFAULT 0 CHECK(parsed IN (0, 1)),
    parser_name TEXT,
    parse_status TEXT NOT NULL DEFAULT 'not_applicable',
    warning TEXT,
    UNIQUE(source_root_id, relative_path)
);
CREATE INDEX IF NOT EXISTS idx_source_files_coverage ON source_files(source_root_id, relevant, parsed);

CREATE TABLE IF NOT EXISTS normalized_facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER REFERENCES products(id),
    canonical_key TEXT NOT NULL,
    canonical_value TEXT NOT NULL,
    value_hash TEXT NOT NULL,
    first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(product_id, canonical_key, value_hash)
);
CREATE INDEX IF NOT EXISTS idx_normalized_facts_lookup ON normalized_facts(product_id, canonical_key);

CREATE TABLE IF NOT EXISTS fact_evidence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    normalized_fact_id INTEGER NOT NULL REFERENCES normalized_facts(id) ON DELETE CASCADE,
    source_id INTEGER REFERENCES sources(id),
    source_root_id INTEGER REFERENCES source_roots(id),
    source_path TEXT,
    line_start INTEGER,
    line_end INTEGER,
    symbol TEXT,
    collector_name TEXT,
    collector_version TEXT,
    extraction_method TEXT NOT NULL,
    trust_class TEXT NOT NULL DEFAULT 'Unknown',
    confidence REAL NOT NULL DEFAULT 0.0,
    evidence_level INTEGER,
    independent_source_group TEXT,
    derived_from_evidence_id INTEGER REFERENCES fact_evidence(id),
    provenance_status TEXT NOT NULL DEFAULT 'partial',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(normalized_fact_id, source_id, source_path, line_start, line_end, collector_name, extraction_method)
);
CREATE INDEX IF NOT EXISTS idx_fact_evidence_fact ON fact_evidence(normalized_fact_id);
CREATE INDEX IF NOT EXISTS idx_fact_evidence_source ON fact_evidence(source_id, source_root_id);

CREATE TABLE IF NOT EXISTS fact_conflicts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER REFERENCES products(id),
    canonical_key TEXT NOT NULL,
    value_a TEXT NOT NULL,
    value_b TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'unresolved' CHECK(status IN ('unresolved', 'resolved', 'explained')),
    explanation TEXT,
    discovered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(product_id, canonical_key, value_a, value_b)
);
CREATE INDEX IF NOT EXISTS idx_fact_conflicts_open ON fact_conflicts(status, canonical_key);

CREATE TABLE IF NOT EXISTS struct_validations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_root_id INTEGER REFERENCES source_roots(id),
    source_path TEXT NOT NULL,
    struct_name TEXT NOT NULL,
    calculated_size INTEGER,
    upstream_size INTEGER,
    status TEXT NOT NULL CHECK(status IN ('validated', 'mismatch', 'unverified')),
    details_json TEXT,
    UNIQUE(source_root_id, source_path, struct_name)
);

CREATE TABLE IF NOT EXISTS command_risks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    normalized_fact_id INTEGER REFERENCES normalized_facts(id) ON DELETE CASCADE,
    risk_class TEXT NOT NULL CHECK(risk_class IN ('read_only', 'volatile_write', 'persistent_write', 'destructive', 'unknown_risk')),
    rationale TEXT NOT NULL,
    UNIQUE(normalized_fact_id)
);

CREATE TABLE IF NOT EXISTS device_reconstructibility (
    product_id INTEGER PRIMARY KEY REFERENCES products(id) ON DELETE CASCADE,
    classification TEXT NOT NULL CHECK(classification IN ('IMPLEMENTATION_READY', 'NEAR_COMPLETE', 'PARTIAL_PROTOCOL', 'IDENTITY_AND_CAPABILITIES', 'IDENTITY_ONLY')),
    family_reconstructibility TEXT NOT NULL,
    device_mapping_confidence REAL NOT NULL DEFAULT 0.0,
    hardware_validation_state TEXT NOT NULL DEFAULT 'pending',
    rationale TEXT NOT NULL,
    audited_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_reconstructibility_class ON device_reconstructibility(classification);

-- Repair pass 2: scope-aware fact identity and typed executable operations.
CREATE TABLE IF NOT EXISTS protocol_operations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    operation_key TEXT NOT NULL UNIQUE,
    scope_type TEXT NOT NULL CHECK(scope_type IN ('device', 'protocol_family')),
    scope_key TEXT NOT NULL,
    product_id INTEGER REFERENCES products(id) ON DELETE CASCADE,
    protocol_family_id INTEGER REFERENCES protocol_families(id) ON DELETE CASCADE,
    protocol_family TEXT,
    semantic TEXT NOT NULL,
    transport TEXT,
    api_semantics TEXT,
    report_id TEXT,
    api_length INTEGER,
    wire_length INTEGER,
    direction TEXT CHECK(direction IN ('host_to_device', 'device_to_host', 'bidirectional')),
    request_encoding_json TEXT,
    response_encoding_json TEXT,
    checksum_json TEXT,
    sequencing_json TEXT,
    initialization_json TEXT,
    capability_mapping_json TEXT,
    confidence REAL NOT NULL DEFAULT 0.0,
    source_trust TEXT NOT NULL DEFAULT 'Unknown',
    operation_status TEXT NOT NULL DEFAULT 'candidate' CHECK(operation_status IN ('candidate', 'implemented', 'observed', 'hardware_verified', 'rejected')),
    source_fact_id INTEGER REFERENCES normalized_facts(id),
    request_method TEXT,
    response_method TEXT,
    opcode TEXT,
    command_class TEXT,
    command_id TEXT,
    endpoint INTEGER,
    interface INTEGER,
    usage_page INTEGER,
    usage INTEGER,
    report_id_in_buffer INTEGER,
    dynamic_fields_json TEXT,
    preconditions_json TEXT,
    timeout_ms INTEGER,
    delay_ms INTEGER,
    side_effect TEXT,
    persistence TEXT,
    risk_state TEXT,
    production_safe INTEGER NOT NULL DEFAULT 0 CHECK(production_safe IN (0,1)),
    CHECK((scope_type='device' AND product_id IS NOT NULL) OR
          (scope_type='protocol_family' AND protocol_family IS NOT NULL)),
    UNIQUE(product_id, semantic, protocol_family, report_id, direction)
);
CREATE INDEX IF NOT EXISTS idx_protocol_operations_product ON protocol_operations(product_id, protocol_family);

CREATE TABLE IF NOT EXISTS source_lineage (
    child_source_root_id INTEGER NOT NULL REFERENCES source_roots(id) ON DELETE CASCADE,
    parent_source_root_id INTEGER REFERENCES source_roots(id) ON DELETE SET NULL,
    relationship TEXT NOT NULL CHECK(relationship IN ('fork', 'derived', 'copied', 'independent', 'unknown')),
    rationale TEXT NOT NULL,
    PRIMARY KEY(child_source_root_id, parent_source_root_id)
);

CREATE TABLE IF NOT EXISTS protocol_families (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    family_key TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    generation TEXT,
    transport_variant TEXT,
    reconstructibility TEXT NOT NULL DEFAULT 'IDENTITY_ONLY',
    hardware_validation_state TEXT NOT NULL DEFAULT 'pending',
    production_safe INTEGER NOT NULL DEFAULT 0 CHECK(production_safe IN (0,1))
);

CREATE TABLE IF NOT EXISTS device_protocol_mappings (
    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    protocol_family_id INTEGER NOT NULL REFERENCES protocol_families(id) ON DELETE CASCADE,
    confidence REAL NOT NULL,
    mapping_basis TEXT NOT NULL,
    evidence_id INTEGER REFERENCES fact_evidence(id),
    source_file_id INTEGER REFERENCES source_files(id),
    PRIMARY KEY(product_id, protocol_family_id)
);

CREATE TABLE IF NOT EXISTS typed_facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fact_type TEXT NOT NULL,
    scope_type TEXT NOT NULL,
    scope_key TEXT NOT NULL,
    semantic_type TEXT NOT NULL,
    canonical_key TEXT NOT NULL,
    canonical_value_json TEXT NOT NULL,
    value_hash TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 0.0,
    UNIQUE(fact_type, scope_type, scope_key, semantic_type, canonical_key, value_hash)
);
CREATE INDEX IF NOT EXISTS idx_typed_facts_scope ON typed_facts(scope_type, scope_key, semantic_type);

CREATE TABLE IF NOT EXISTS typed_fact_evidence (
    typed_fact_id INTEGER NOT NULL REFERENCES typed_facts(id) ON DELETE CASCADE,
    source_file_id INTEGER REFERENCES source_files(id),
    source_id INTEGER REFERENCES sources(id),
    line_start INTEGER,
    line_end INTEGER,
    symbol TEXT,
    extraction_method TEXT NOT NULL,
    trust_class TEXT NOT NULL,
    lineage_group TEXT NOT NULL,
    confidence REAL NOT NULL,
    provenance_status TEXT NOT NULL,
    artifact_sha256 TEXT,
    external_url TEXT,
    PRIMARY KEY(typed_fact_id, source_file_id, extraction_method)
);
CREATE INDEX IF NOT EXISTS idx_typed_evidence_lineage ON typed_fact_evidence(typed_fact_id, lineage_group);

CREATE TABLE IF NOT EXISTS packet_layouts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope_type TEXT NOT NULL,
    scope_key TEXT NOT NULL,
    layout_name TEXT NOT NULL,
    variant TEXT NOT NULL DEFAULT 'default',
    transport TEXT,
    struct_size INTEGER,
    api_payload_length INTEGER,
    api_buffer_length INTEGER,
    wire_length INTEGER,
    report_id TEXT,
    report_id_in_buffer INTEGER CHECK(report_id_in_buffer IN (0,1) OR report_id_in_buffer IS NULL),
    endianness TEXT,
    validation_status TEXT NOT NULL DEFAULT 'unresolved',
    source_file_id INTEGER REFERENCES source_files(id),
    UNIQUE(scope_type, scope_key, layout_name, variant, source_file_id)
);
CREATE INDEX IF NOT EXISTS idx_packet_layout_scope ON packet_layouts(scope_type, scope_key);

CREATE TABLE IF NOT EXISTS packet_fields (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    packet_layout_id INTEGER NOT NULL REFERENCES packet_layouts(id) ON DELETE CASCADE,
    field_name TEXT NOT NULL,
    byte_offset INTEGER,
    bit_offset INTEGER,
    size_bytes INTEGER,
    field_type TEXT,
    expression TEXT,
    endianness TEXT,
    dynamic INTEGER NOT NULL DEFAULT 0 CHECK(dynamic IN (0,1)),
    UNIQUE(packet_layout_id, field_name, byte_offset, bit_offset)
);

CREATE TABLE IF NOT EXISTS protocol_sequences (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope_type TEXT NOT NULL,
    scope_key TEXT NOT NULL,
    sequence_key TEXT NOT NULL,
    semantic TEXT NOT NULL,
    preconditions_json TEXT,
    retry_policy_json TEXT,
    timeout_ms INTEGER,
    source_file_id INTEGER REFERENCES source_files(id),
    UNIQUE(scope_type, scope_key, sequence_key, source_file_id)
);
CREATE INDEX IF NOT EXISTS idx_sequences_scope ON protocol_sequences(scope_type, scope_key);

CREATE TABLE IF NOT EXISTS protocol_sequence_steps (
    sequence_id INTEGER NOT NULL REFERENCES protocol_sequences(id) ON DELETE CASCADE,
    step_index INTEGER NOT NULL,
    operation_id INTEGER REFERENCES protocol_operations(id),
    step_kind TEXT NOT NULL,
    condition_json TEXT,
    expected_response_json TEXT,
    delay_ms INTEGER,
    PRIMARY KEY(sequence_id, step_index)
);

CREATE TABLE IF NOT EXISTS runtime_observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope_type TEXT NOT NULL,
    scope_key TEXT NOT NULL,
    observation_kind TEXT NOT NULL,
    semantic_label TEXT,
    value_json TEXT NOT NULL,
    source_file_id INTEGER REFERENCES source_files(id),
    trust_class TEXT NOT NULL,
    hardware_verified INTEGER NOT NULL DEFAULT 0 CHECK(hardware_verified IN (0,1)),
    UNIQUE(scope_type, scope_key, observation_kind, value_json, source_file_id)
);

CREATE TABLE IF NOT EXISTS capture_files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_file_id INTEGER NOT NULL UNIQUE REFERENCES source_files(id) ON DELETE CASCADE,
    sha256 TEXT NOT NULL,
    capture_format TEXT NOT NULL,
    packet_count INTEGER NOT NULL DEFAULT 0,
    transaction_count INTEGER NOT NULL DEFAULT 0,
    parse_status TEXT NOT NULL,
    semantic_label TEXT
);

CREATE TABLE IF NOT EXISTS capture_transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    capture_file_id INTEGER NOT NULL REFERENCES capture_files(id) ON DELETE CASCADE,
    sequence_no INTEGER NOT NULL,
    timestamp REAL,
    vid INTEGER,
    pid INTEGER,
    interface INTEGER,
    endpoint INTEGER,
    transfer_type TEXT,
    direction TEXT,
    control_request_json TEXT,
    report_id INTEGER,
    payload_hex TEXT NOT NULL,
    payload_length INTEGER NOT NULL,
    pair_key TEXT,
    UNIQUE(capture_file_id, sequence_no)
);
CREATE INDEX IF NOT EXISTS idx_capture_transactions_device ON capture_transactions(vid, pid, capture_file_id);

CREATE TABLE IF NOT EXISTS operation_evidence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    operation_id INTEGER NOT NULL REFERENCES protocol_operations(id) ON DELETE CASCADE,
    source_file_id INTEGER REFERENCES source_files(id),
    source_id INTEGER REFERENCES sources(id),
    extraction_method TEXT NOT NULL,
    trust_class TEXT NOT NULL,
    lineage_group TEXT NOT NULL,
    confidence REAL NOT NULL,
    line_start INTEGER,
    line_end INTEGER,
    symbol TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_operation_evidence_file_unique
    ON operation_evidence(operation_id, source_file_id, extraction_method)
    WHERE source_file_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_operation_evidence_source_unique
    ON operation_evidence(operation_id, source_id, extraction_method)
    WHERE source_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_operation_evidence_lineage ON operation_evidence(operation_id, lineage_group);

CREATE TABLE IF NOT EXISTS operation_completeness (
    operation_id INTEGER PRIMARY KEY REFERENCES protocol_operations(id) ON DELETE CASCADE,
    score INTEGER NOT NULL,
    missing_requirements_json TEXT NOT NULL,
    complete INTEGER NOT NULL CHECK(complete IN (0,1)),
    explanation TEXT NOT NULL
);

-- Persistent, non-semantic cache.  It is safe to retain across derived-data
-- rebuilds because size + nanosecond mtime guard every lookup.
CREATE TABLE IF NOT EXISTS source_content_cache (
    absolute_path TEXT PRIMARY KEY,
    size INTEGER NOT NULL,
    mtime_ns INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    verified_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS external_attachments (
    external_id TEXT PRIMARY KEY,
    source_name TEXT NOT NULL,
    issue_iid INTEGER,
    issue_url TEXT,
    attachment_url TEXT NOT NULL UNIQUE,
    filename TEXT NOT NULL,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    content_sha256 TEXT,
    size INTEGER,
    content_type TEXT,
    error TEXT,
    source_created_at TEXT,
    last_checked TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_external_attachments_status ON external_attachments(source_name,status,kind);
"""
