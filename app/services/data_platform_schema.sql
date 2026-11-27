CREATE TABLE IF NOT EXISTS platform_migrations (
    version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS local_accounts (
    id TEXT PRIMARY KEY, login_name TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1))
);
CREATE TABLE IF NOT EXISTS account_households (
    account_id TEXT NOT NULL REFERENCES local_accounts(id) ON DELETE CASCADE,
    household_id INTEGER NOT NULL REFERENCES households(id),
    role TEXT NOT NULL CHECK(role IN ('owner','editor','viewer')),
    PRIMARY KEY(account_id,household_id)
);
CREATE INDEX IF NOT EXISTS account_household_lookup ON account_households(household_id);
CREATE TABLE IF NOT EXISTS auth_sessions (
    token_hash TEXT PRIMARY KEY, account_id TEXT NOT NULL,
    household_id INTEGER NOT NULL, expires_at INTEGER NOT NULL,
    created_at INTEGER NOT NULL, revoked_at INTEGER,
    FOREIGN KEY(account_id,household_id) REFERENCES account_households(account_id,household_id) ON DELETE CASCADE,
    CHECK(expires_at > created_at)
);
CREATE INDEX IF NOT EXISTS auth_session_account ON auth_sessions(account_id,household_id);
CREATE INDEX IF NOT EXISTS auth_session_expiry ON auth_sessions(expires_at);

CREATE TABLE IF NOT EXISTS catalog_data_sources (
    code TEXT PRIMARY KEY, name TEXT NOT NULL, documentation_url TEXT NOT NULL,
    rights_status TEXT NOT NULL CHECK(rights_status IN ('review_required','approved'))
);
INSERT OR IGNORE INTO catalog_data_sources VALUES
 ('mfds_recipe','식약처 COOKRCP01','https://www.foodsafetykorea.go.kr/api/newDatasetDetail.do?svc_no=COOKRCP01','review_required'),
 ('mfds_nutrition','식약처 식품영양성분DB','https://various.foodsafetykorea.go.kr/nutrient/industry/openApi/info.do','review_required'),
 ('10000recipe','만개의레시피 제휴 데이터','https://www.10000recipe.com/company/intro.html','review_required');
CREATE TABLE IF NOT EXISTS data_releases (
    id TEXT PRIMARY KEY, source_code TEXT NOT NULL REFERENCES catalog_data_sources(code),
    status TEXT NOT NULL CHECK(status IN ('staging','validated','published','quarantined')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    validation_report TEXT, source_revision TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS release_source ON data_releases(source_code);
CREATE TABLE IF NOT EXISTS source_records (
    release_id TEXT NOT NULL REFERENCES data_releases(id),
    external_id TEXT NOT NULL, kind TEXT NOT NULL CHECK(kind IN ('food','recipe')),
    payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
    payload_sha256 TEXT NOT NULL,
    PRIMARY KEY(release_id,external_id)
);
CREATE TABLE IF NOT EXISTS catalog_foods (
    release_id TEXT NOT NULL, external_id TEXT NOT NULL, name TEXT NOT NULL,
    basis_amount REAL NOT NULL CHECK(basis_amount>0),
    basis_unit TEXT NOT NULL CHECK(basis_unit IN ('g','ml')),
    PRIMARY KEY(release_id,external_id),
    FOREIGN KEY(release_id,external_id) REFERENCES source_records(release_id,external_id)
);
CREATE TABLE IF NOT EXISTS catalog_food_nutrients (
    release_id TEXT NOT NULL, food_id TEXT NOT NULL,
    nutrient TEXT NOT NULL CHECK(nutrient IN ('energy_kcal','protein_g','carbohydrate_g','fat_g','sodium_mg','sugar_g')),
    amount REAL NOT NULL CHECK(amount>=0),
    PRIMARY KEY(release_id,food_id,nutrient),
    FOREIGN KEY(release_id,food_id) REFERENCES catalog_foods(release_id,external_id)
);
CREATE TABLE IF NOT EXISTS catalog_recipes (
    release_id TEXT NOT NULL, external_id TEXT NOT NULL, name TEXT NOT NULL,
    servings REAL CHECK(servings>0), cook_time_minutes INTEGER CHECK(cook_time_minutes>0),
    ingredients_text TEXT NOT NULL,
    PRIMARY KEY(release_id,external_id),
    FOREIGN KEY(release_id,external_id) REFERENCES source_records(release_id,external_id)
);
CREATE TABLE IF NOT EXISTS catalog_recipe_steps (
    release_id TEXT NOT NULL, recipe_id TEXT NOT NULL, step_no INTEGER NOT NULL CHECK(step_no>0),
    instruction TEXT NOT NULL, PRIMARY KEY(release_id,recipe_id,step_no),
    FOREIGN KEY(release_id,recipe_id) REFERENCES catalog_recipes(release_id,external_id)
);
CREATE TABLE IF NOT EXISTS catalog_recipe_nutrients (
    release_id TEXT NOT NULL, recipe_id TEXT NOT NULL,
    nutrient TEXT NOT NULL CHECK(nutrient IN ('energy_kcal','protein_g','carbohydrate_g','fat_g','sodium_mg','sugar_g')),
    amount REAL NOT NULL CHECK(amount>=0),
    basis_amount REAL NOT NULL CHECK(basis_amount>0),
    basis_unit TEXT NOT NULL CHECK(basis_unit IN ('g','ml','serving')),
    PRIMARY KEY(release_id,recipe_id,nutrient),
    FOREIGN KEY(release_id,recipe_id) REFERENCES catalog_recipes(release_id,external_id)
);
-- Explicit, reviewed links; never join by food name or equal numeric IDs.
CREATE TABLE IF NOT EXISTS catalog_ingredient_links (
    ingredient_id INTEGER NOT NULL REFERENCES ingredients(id), release_id TEXT NOT NULL,
    food_id TEXT NOT NULL, reviewed_at TEXT NOT NULL,
    PRIMARY KEY(ingredient_id,release_id),
    FOREIGN KEY(release_id,food_id) REFERENCES catalog_foods(release_id,external_id)
);
CREATE INDEX IF NOT EXISTS catalog_link_food ON catalog_ingredient_links(release_id,food_id);
CREATE TABLE IF NOT EXISTS catalog_recipe_links (
    recipe_id INTEGER NOT NULL REFERENCES recipes(id), release_id TEXT NOT NULL,
    external_id TEXT NOT NULL, reviewed_at TEXT NOT NULL,
    PRIMARY KEY(recipe_id,release_id),
    FOREIGN KEY(release_id,external_id) REFERENCES catalog_recipes(release_id,external_id)
);
CREATE INDEX IF NOT EXISTS catalog_link_recipe ON catalog_recipe_links(release_id,external_id);
CREATE TABLE IF NOT EXISTS active_data_releases (
    source_code TEXT PRIMARY KEY REFERENCES catalog_data_sources(code),
    release_id TEXT NOT NULL REFERENCES data_releases(id), cache_version INTEGER NOT NULL CHECK(cache_version>0)
);
CREATE INDEX IF NOT EXISTS active_release_lookup ON active_data_releases(release_id);
CREATE TABLE IF NOT EXISTS meal_data_context (
    meal_session_id INTEGER NOT NULL REFERENCES meal_sessions(id),
    source_code TEXT NOT NULL REFERENCES catalog_data_sources(code),
    release_id TEXT NOT NULL REFERENCES data_releases(id),
    PRIMARY KEY(meal_session_id,source_code)
);
CREATE INDEX IF NOT EXISTS meal_context_release ON meal_data_context(release_id);
CREATE INDEX IF NOT EXISTS member_household_lookup ON members(household_id);
CREATE INDEX IF NOT EXISTS inventory_household_ingredient ON inventory_items(household_id,ingredient_id);
CREATE INDEX IF NOT EXISTS meal_household_lookup ON meal_sessions(household_id);
CREATE INDEX IF NOT EXISTS participant_member_lookup ON meal_session_participants(member_id);
CREATE INDEX IF NOT EXISTS meal_record_session_lookup ON meal_records(session_id);
CREATE INDEX IF NOT EXISTS meal_item_member_lookup ON meal_record_items(member_id,record_id);
CREATE INDEX IF NOT EXISTS intake_item_lookup ON nutrition_intakes(record_item_id);
INSERT OR IGNORE INTO platform_migrations(version) VALUES(1);
CREATE TABLE IF NOT EXISTS inventory_events (
    id INTEGER PRIMARY KEY,
    household_id INTEGER NOT NULL REFERENCES households(id),
    client_event_id TEXT NOT NULL,
    source TEXT NOT NULL CHECK(source IN ('mock','yolo','manual')),
    action TEXT NOT NULL CHECK(action IN ('stock_in','stock_out')),
    detected_label TEXT NOT NULL,
    confidence REAL CHECK(confidence>=0 AND confidence<=1),
    payload_sha256 TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending_confirmation' CHECK(status IN ('pending_confirmation','applied','rejected')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    applied_at TEXT, inventory_item_id INTEGER REFERENCES inventory_items(id),
    confirmation_sha256 TEXT,
    UNIQUE(household_id,client_event_id)
);
CREATE INDEX IF NOT EXISTS inventory_events_household_created ON inventory_events(household_id,id);
CREATE INDEX IF NOT EXISTS inventory_events_item ON inventory_events(inventory_item_id);
CREATE TABLE IF NOT EXISTS inventory_sync_state (
    household_id INTEGER PRIMARY KEY REFERENCES households(id),
    inventory_version INTEGER NOT NULL DEFAULT 0 CHECK(inventory_version>=0),
    last_applied_at TEXT
);
INSERT OR IGNORE INTO platform_migrations(version) VALUES(2);
CREATE TABLE IF NOT EXISTS inventory_event_changes (
    event_id INTEGER PRIMARY KEY REFERENCES inventory_events(id),
    ingredient_id INTEGER NOT NULL REFERENCES ingredients(id),
    inventory_item_id INTEGER NOT NULL REFERENCES inventory_items(id),
    quantity_g REAL NOT NULL CHECK(quantity_g>0),
    before_quantity_g REAL NOT NULL CHECK(before_quantity_g>=0),
    after_quantity_g REAL NOT NULL CHECK(after_quantity_g>=0),
    confirmed_by TEXT NOT NULL REFERENCES local_accounts(id),
    confirmed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS event_change_ingredient ON inventory_event_changes(ingredient_id);
CREATE INDEX IF NOT EXISTS event_change_item ON inventory_event_changes(inventory_item_id);
CREATE INDEX IF NOT EXISTS event_change_account ON inventory_event_changes(confirmed_by);

-- Account-only sessions bootstrap households; never accepted as inventory sessions.
CREATE TABLE IF NOT EXISTS local_account_sessions (
    token_hash TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES local_accounts(id) ON DELETE CASCADE,
    created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL, revoked_at INTEGER,
    CHECK(expires_at > created_at)
);
CREATE INDEX IF NOT EXISTS local_account_session_account ON local_account_sessions(account_id);
CREATE INDEX IF NOT EXISTS local_account_session_expiry ON local_account_sessions(expires_at);
INSERT OR IGNORE INTO platform_migrations(version) VALUES(3);

CREATE TABLE IF NOT EXISTS meal_account_feedback (
    record_id INTEGER NOT NULL REFERENCES meal_records(id) ON DELETE CASCADE,
    account_id TEXT NOT NULL REFERENCES local_accounts(id) ON DELETE CASCADE,
    taste TEXT CHECK (taste IN ('bad', 'okay', 'good')),
    salt TEXT CHECK (salt IN ('bland', 'balanced', 'salty')),
    difficulty TEXT CHECK (difficulty IN ('easy', 'normal', 'hard')),
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (record_id, account_id),
    CHECK (taste IS NOT NULL OR salt IS NOT NULL OR difficulty IS NOT NULL)
);
