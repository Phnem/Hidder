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

-- Commercial model inventory.  This is deliberately an additive layer: the
-- legacy ``products`` table remains the source-local/technical product graph
-- and is never re-purposed as a marketing model catalogue.
CREATE TABLE IF NOT EXISTS commercial_models (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    brand_id INTEGER NOT NULL REFERENCES brands(id),
    canonical_name TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    category TEXT,
    product_line TEXT,
    lifecycle_status TEXT NOT NULL DEFAULT 'CURRENT' CHECK(lifecycle_status IN ('CURRENT', 'DISCONTINUED', 'UNKNOWN')),
    candidate_only INTEGER NOT NULL DEFAULT 0 CHECK(candidate_only IN (0,1)),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(brand_id, normalized_name)
);
CREATE INDEX IF NOT EXISTS idx_commercial_models_brand ON commercial_models(brand_id);

CREATE TABLE IF NOT EXISTS model_variants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    commercial_model_id INTEGER NOT NULL REFERENCES commercial_models(id) ON DELETE CASCADE,
    canonical_name TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    variant_label TEXT NOT NULL DEFAULT 'DEFAULT',
    generation TEXT,
    model_code TEXT,
    lifecycle_status TEXT NOT NULL DEFAULT 'CURRENT' CHECK(lifecycle_status IN ('CURRENT', 'DISCONTINUED', 'UNKNOWN')),
    source_product_id INTEGER REFERENCES products(id),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(commercial_model_id, normalized_name)
);
CREATE INDEX IF NOT EXISTS idx_model_variants_model ON model_variants(commercial_model_id);
CREATE INDEX IF NOT EXISTS idx_model_variants_source_product ON model_variants(source_product_id);

CREATE TABLE IF NOT EXISTS model_aliases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    commercial_model_id INTEGER REFERENCES commercial_models(id) ON DELETE CASCADE,
    model_variant_id INTEGER REFERENCES model_variants(id) ON DELETE CASCADE,
    alias_name TEXT NOT NULL,
    normalized_alias TEXT NOT NULL,
    alias_kind TEXT NOT NULL DEFAULT 'FORMAT',
    source_url TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CHECK(commercial_model_id IS NOT NULL OR model_variant_id IS NOT NULL),
    UNIQUE(model_variant_id, normalized_alias)
);

CREATE TABLE IF NOT EXISTS model_evidence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    commercial_model_id INTEGER REFERENCES commercial_models(id) ON DELETE CASCADE,
    model_variant_id INTEGER REFERENCES model_variants(id) ON DELETE CASCADE,
    evidence_class TEXT NOT NULL,
    source_id INTEGER REFERENCES sources(id),
    source_file_id INTEGER REFERENCES source_files(id),
    source_url TEXT,
    source_path TEXT,
    extraction_method TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 0.0,
    details_json TEXT,
    observed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CHECK(commercial_model_id IS NOT NULL OR model_variant_id IS NOT NULL),
    UNIQUE(model_variant_id, evidence_class, source_url, source_path, extraction_method)
);
CREATE INDEX IF NOT EXISTS idx_model_evidence_variant ON model_evidence(model_variant_id);

-- ``usb_device_identities`` is the technical identity node.  A model binding
-- can be many-to-many and keeps its confidence separate from VID/PID itself.
CREATE TABLE IF NOT EXISTS usb_device_identities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vid INTEGER NOT NULL,
    pid INTEGER NOT NULL,
    vid_hex TEXT NOT NULL,
    pid_hex TEXT NOT NULL,
    interface_number INTEGER,
    usage_page INTEGER,
    usage INTEGER,
    bcd_device TEXT,
    manufacturer_string TEXT,
    product_string TEXT,
    identity_role TEXT NOT NULL DEFAULT 'PERIPHERAL' CHECK(identity_role IN ('PERIPHERAL', 'RECEIVER', 'BOOTLOADER', 'FIRMWARE_UPDATE', 'UNKNOWN')),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(vid, pid, interface_number, usage_page, usage, bcd_device, identity_role)
);
CREATE INDEX IF NOT EXISTS idx_usb_device_identities_vid_pid ON usb_device_identities(vid, pid);

CREATE TABLE IF NOT EXISTS model_identity_bindings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    model_variant_id INTEGER NOT NULL REFERENCES model_variants(id) ON DELETE CASCADE,
    usb_device_identity_id INTEGER NOT NULL REFERENCES usb_device_identities(id) ON DELETE CASCADE,
    binding_role TEXT NOT NULL DEFAULT 'PERIPHERAL' CHECK(binding_role IN ('PERIPHERAL', 'WIRED', 'RECEIVER', 'BOOTLOADER', 'FIRMWARE_UPDATE', 'WIRELESS_CHILD', 'UNKNOWN')),
    binding_confidence TEXT NOT NULL CHECK(binding_confidence IN ('EXACT_OFFICIAL', 'EXACT_RUNTIME_OBSERVED', 'EXACT_STATIC_IMPLEMENTATION', 'STRONG_MULTI_SOURCE', 'CANDIDATE', 'AMBIGUOUS')),
    source_device_identifier_id INTEGER REFERENCES device_identifiers(id),
    evidence_id INTEGER REFERENCES model_evidence(id),
    provenance TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(model_variant_id, usb_device_identity_id, binding_role, source_device_identifier_id)
);
CREATE INDEX IF NOT EXISTS idx_model_identity_identity ON model_identity_bindings(usb_device_identity_id);

CREATE TABLE IF NOT EXISTS model_transports (
    model_variant_id INTEGER NOT NULL REFERENCES model_variants(id) ON DELETE CASCADE,
    transport TEXT NOT NULL CHECK(transport IN ('WIRED_USB', 'USB_2_4G_RECEIVER', 'BLUETOOTH_HID', 'OTHER', 'UNKNOWN')),
    evidence_id INTEGER REFERENCES model_evidence(id),
    confidence REAL NOT NULL DEFAULT 0.0,
    PRIMARY KEY(model_variant_id, transport)
);

CREATE TABLE IF NOT EXISTS receiver_bindings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    peripheral_variant_id INTEGER NOT NULL REFERENCES model_variants(id) ON DELETE CASCADE,
    receiver_identity_id INTEGER NOT NULL REFERENCES usb_device_identities(id) ON DELETE CASCADE,
    paired_protocol_info TEXT,
    binding_confidence TEXT NOT NULL CHECK(binding_confidence IN ('EXACT_OFFICIAL', 'EXACT_RUNTIME_OBSERVED', 'EXACT_STATIC_IMPLEMENTATION', 'STRONG_MULTI_SOURCE', 'CANDIDATE', 'AMBIGUOUS')),
    evidence_id INTEGER REFERENCES model_evidence(id),
    UNIQUE(peripheral_variant_id, receiver_identity_id)
);

CREATE TABLE IF NOT EXISTS model_protocol_bindings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    model_variant_id INTEGER NOT NULL REFERENCES model_variants(id) ON DELETE CASCADE,
    protocol_family_id INTEGER NOT NULL REFERENCES protocol_families(id) ON DELETE CASCADE,
    binding_status TEXT NOT NULL CHECK(binding_status IN ('EXACT', 'CANDIDATE', 'AMBIGUOUS')),
    confidence REAL NOT NULL DEFAULT 0.0,
    source_product_id INTEGER REFERENCES products(id),
    evidence_id INTEGER REFERENCES model_evidence(id),
    provenance TEXT NOT NULL,
    UNIQUE(model_variant_id, protocol_family_id, source_product_id)
);
CREATE INDEX IF NOT EXISTS idx_model_protocol_variant ON model_protocol_bindings(model_variant_id);

CREATE TABLE IF NOT EXISTS software_targets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    brand_id INTEGER NOT NULL REFERENCES brands(id),
    target_key TEXT NOT NULL,
    display_name TEXT NOT NULL,
    target_kind TEXT NOT NULL CHECK(target_kind IN ('WEB_CONFIGURATOR', 'NATIVE_APPLICATION', 'FIRMWARE_TOOL', 'DOWNLOAD_PAGE', 'OTHER')),
    official_status TEXT NOT NULL DEFAULT 'UNKNOWN' CHECK(official_status IN ('OFFICIAL', 'OBSERVED', 'UNKNOWN')),
    target_url TEXT,
    source_path TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(brand_id, target_key)
);
CREATE INDEX IF NOT EXISTS idx_software_targets_brand ON software_targets(brand_id);

CREATE TABLE IF NOT EXISTS software_model_compatibilities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    software_target_id INTEGER NOT NULL REFERENCES software_targets(id) ON DELETE CASCADE,
    model_variant_id INTEGER NOT NULL REFERENCES model_variants(id) ON DELETE CASCADE,
    compatibility_status TEXT NOT NULL CHECK(compatibility_status IN ('SUPPORTED_OFFICIAL', 'SUPPORTED_OBSERVED', 'NOT_SUPPORTED_OBSERVED', 'AMBIGUOUS')),
    confidence REAL NOT NULL DEFAULT 0.0,
    evidence_id INTEGER REFERENCES model_evidence(id),
    provenance TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(software_target_id, model_variant_id, compatibility_status)
);
CREATE INDEX IF NOT EXISTS idx_software_compat_variant ON software_model_compatibilities(model_variant_id);

-- AI reconciliation staging.  AI output is discovery input only and is kept
-- separate from canonical commercial models until a verification pass opts in
-- to promotion.
CREATE TABLE IF NOT EXISTS ai_model_candidates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_ai TEXT NOT NULL CHECK(source_ai IN ('QWEN', 'GEMINI', 'CLAUDE', 'META_AI')),
    raw_brand TEXT NOT NULL,
    canonical_brand_id INTEGER REFERENCES brands(id),
    brand_status TEXT NOT NULL CHECK(brand_status IN ('CANONICAL', 'NEW_BRAND_CANDIDATE')),
    category TEXT NOT NULL CHECK(category IN ('KEYBOARD', 'MOUSE')),
    raw_model_name TEXT NOT NULL,
    normalized_model_name TEXT NOT NULL,
    input_path TEXT NOT NULL,
    line_number INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source_ai, raw_brand, category, raw_model_name, input_path, line_number)
);
CREATE INDEX IF NOT EXISTS idx_ai_candidates_key ON ai_model_candidates(canonical_brand_id, category, normalized_model_name);

CREATE TABLE IF NOT EXISTS ai_model_unions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_brand_id INTEGER REFERENCES brands(id),
    raw_brand TEXT NOT NULL,
    brand_status TEXT NOT NULL CHECK(brand_status IN ('CANONICAL', 'NEW_BRAND_CANDIDATE')),
    category TEXT NOT NULL CHECK(category IN ('KEYBOARD', 'MOUSE')),
    canonical_candidate_name TEXT NOT NULL,
    normalized_model_name TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(canonical_brand_id, raw_brand, category, normalized_model_name)
);
CREATE INDEX IF NOT EXISTS idx_ai_union_lookup ON ai_model_unions(canonical_brand_id, category, normalized_model_name);

CREATE TABLE IF NOT EXISTS ai_model_votes (
    ai_model_union_id INTEGER NOT NULL REFERENCES ai_model_unions(id) ON DELETE CASCADE,
    source_ai TEXT NOT NULL CHECK(source_ai IN ('QWEN', 'GEMINI', 'CLAUDE', 'META_AI')),
    candidate_count INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY(ai_model_union_id, source_ai)
);

CREATE TABLE IF NOT EXISTS model_verification_evidence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ai_model_union_id INTEGER NOT NULL REFERENCES ai_model_unions(id) ON DELETE CASCADE,
    evidence_tier TEXT NOT NULL CHECK(evidence_tier IN ('TIER_1_OFFICIAL', 'TIER_2_STRONG', 'TIER_3_CANDIDATE')),
    evidence_class TEXT NOT NULL,
    source_url TEXT,
    source_ref TEXT,
    category TEXT,
    details_json TEXT,
    UNIQUE(ai_model_union_id, evidence_tier, evidence_class, source_url, source_ref)
);
CREATE INDEX IF NOT EXISTS idx_verification_evidence_union ON model_verification_evidence(ai_model_union_id);

CREATE TABLE IF NOT EXISTS model_reconciliation_results (
    ai_model_union_id INTEGER PRIMARY KEY REFERENCES ai_model_unions(id) ON DELETE CASCADE,
    status TEXT NOT NULL CHECK(status IN ('VERIFIED_OFFICIAL', 'VERIFIED_STRONG', 'UNRESOLVED', 'REJECTED_NOT_MODEL', 'REJECTED_WRONG_BRAND', 'REJECTED_WRONG_CATEGORY', 'REJECTED_DUPLICATE', 'COSMETIC_VARIANT', 'ACCESSORY', 'RECEIVER', 'SOFTWARE_ARTIFACT', 'FIRMWARE_ARTIFACT')),
    classification TEXT NOT NULL CHECK(classification IN ('COMMERCIAL_MODEL', 'HARDWARE_VARIANT', 'COSMETIC_SKU', 'ALIAS', 'RECEIVER', 'ARTIFACT', 'NOT_MODEL', 'UNKNOWN')),
    reason TEXT NOT NULL,
    promoted_model_variant_id INTEGER REFERENCES model_variants(id),
    reviewed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Official domains are evidence-bearing facts rather than a one-off static
-- whitelist.  Subdomains and sibling product hosts can be recognised through
-- a configured official endpoint or a recorded vendor-owned source.
CREATE TABLE IF NOT EXISTS official_brand_domains (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    brand_id INTEGER NOT NULL REFERENCES brands(id),
    hostname TEXT NOT NULL,
    registrable_domain TEXT NOT NULL,
    provenance TEXT NOT NULL,
    source_url TEXT,
    confidence REAL NOT NULL DEFAULT 1.0,
    discovered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(brand_id, hostname, provenance, source_url)
);
CREATE INDEX IF NOT EXISTS idx_official_brand_domains_lookup ON official_brand_domains(brand_id, hostname);

CREATE TABLE IF NOT EXISTS legacy_category_conflicts (
    product_id INTEGER PRIMARY KEY REFERENCES products(id),
    legacy_category TEXT NOT NULL,
    authoritative_category TEXT NOT NULL CHECK(authoritative_category IN ('KEYBOARD', 'MOUSE')),
    official_source_url TEXT NOT NULL,
    rationale TEXT NOT NULL,
    detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""
