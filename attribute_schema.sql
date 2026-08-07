-- Haqdaar Voice — Attribute Matrix v1
-- SUPERSEDED (v5, see CHANGE_REPORT.md #7): the `schemes` table here uses the
-- old scheme_id/menu-path dial code. ingest.py's inline SCHEMA is the live
-- schema (scheme_no dial codes, theme/need_group/applicant_type columns).
-- Keep this file only for the three-valued matching (satisfied/violated/
-- unknown) reference query in section 5 — do not run it against the DB.
--
-- The single contract that the question bank, the scheme catalogue and the
-- narrowing engine all agree on. Nothing may reference an attribute that is
-- not in `attributes`. Nothing may write a value that is not in `attr_values`.

PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------------------
-- 1. Scheme catalogue (unchanged from PRD §4, plus routing columns)
-- ---------------------------------------------------------------------------
CREATE TABLE schemes (
  scheme_id         TEXT PRIMARY KEY,
  name              TEXT NOT NULL,
  benefit_one_line  TEXT,          -- <= 25 words, spoken at PRESENT
  eligibility       TEXT,
  documents         TEXT,
  where_to_apply    TEXT,
  deadline          TEXT,
  source_link       TEXT,
  source_date       TEXT,
  verified          INTEGER NOT NULL DEFAULT 0,   -- PRD §4 honesty rule
  level             TEXT CHECK (level IN ('central','state')),
  state_scope       TEXT,          -- NULL = all-India; else state code
  purpose           TEXT           -- primary purpose tag, see attr_values('purpose')
);

CREATE VIRTUAL TABLE schemes_fts USING fts5(
  name, benefit_one_line, eligibility, documents,
  content='schemes', content_rowid='rowid'
);

-- ---------------------------------------------------------------------------
-- 2. The attribute dictionary — the ONLY legal column space
-- ---------------------------------------------------------------------------
CREATE TABLE attributes (
  attribute   TEXT PRIMARY KEY,
  kind        TEXT NOT NULL CHECK (kind IN ('enum','bool','band','int','session')),
  label_en    TEXT NOT NULL,
  notes       TEXT
);

CREATE TABLE attr_values (
  attribute   TEXT NOT NULL REFERENCES attributes(attribute),
  value       TEXT NOT NULL,
  label_en    TEXT,
  ord         INTEGER,             -- for band ordering (gte/lte comparisons)
  PRIMARY KEY (attribute, value)
);

-- ---------------------------------------------------------------------------
-- 3. Scheme rules — THREE-VALUED, this is the whole trick
-- ---------------------------------------------------------------------------
-- A scheme is eliminated ONLY when a hard rule is actively contradicted by a
-- known answer. An unanswered attribute NEVER eliminates a scheme.
--   satisfied  -> keep, +weight
--   violated   -> eliminate (hard=1) or -weight (hard=0)
--   unknown    -> keep, no score change
-- This is why plain `WHERE land_band = '1-2'` is wrong: it silently drops
-- every scheme whose land rule we simply haven't asked about yet.
CREATE TABLE scheme_rules (
  scheme_id    TEXT NOT NULL REFERENCES schemes(scheme_id),
  attribute    TEXT NOT NULL REFERENCES attributes(attribute),
  op           TEXT NOT NULL CHECK (op IN ('in','not_in','gte','lte','eq','any')),
  value        TEXT NOT NULL,      -- JSON array for in/not_in, scalar otherwise
  hard         INTEGER NOT NULL DEFAULT 1,   -- 1 = disqualifier, 0 = preference
  weight       REAL NOT NULL DEFAULT 1.0,
  source_quote TEXT,               -- verbatim line from the scheme PDF. Audit trail.
  PRIMARY KEY (scheme_id, attribute, op, value)
);

CREATE INDEX idx_rules_attr ON scheme_rules(attribute);

-- ---------------------------------------------------------------------------
-- 4. Session state (in-memory at runtime; table shown for shape only)
-- ---------------------------------------------------------------------------
CREATE TABLE call_answers (
  call_sid    TEXT NOT NULL,
  attribute   TEXT NOT NULL REFERENCES attributes(attribute),
  value       TEXT NOT NULL,
  source      TEXT NOT NULL CHECK (source IN ('dtmf','speech','inferred','default')),
  asked_by    TEXT,                -- question id
  turn_no     INTEGER,
  confidence  REAL,
  PRIMARY KEY (call_sid, attribute)
);

-- PRD §7: profile answers are NOT persisted past the call. This table is
-- session-scoped and dropped/anonymised at call end. Only `scheme_searches`
-- (scheme_id, timestamp, caller_hash) survives.
CREATE TABLE scheme_searches (
  caller_hash TEXT NOT NULL,
  scheme_id   TEXT NOT NULL REFERENCES schemes(scheme_id),
  shown_at    TEXT NOT NULL,
  presented   INTEGER DEFAULT 1,
  detailed    INTEGER DEFAULT 0
);

-- ---------------------------------------------------------------------------
-- 5. THE narrowing query (parameterised; engine builds :answers as a temp table)
-- ---------------------------------------------------------------------------
-- CREATE TEMP TABLE ans(attribute TEXT PRIMARY KEY, value TEXT, ord INTEGER);
--
-- SELECT s.scheme_id, s.name, s.benefit_one_line, s.verified,
--        COALESCE(SUM(CASE WHEN r.hard = 0 THEN r.weight END), 0) AS boost
-- FROM schemes s
-- LEFT JOIN scheme_rules r ON r.scheme_id = s.scheme_id
-- WHERE NOT EXISTS (
--   SELECT 1 FROM scheme_rules v
--   JOIN ans a ON a.attribute = v.attribute        -- only ANSWERED attributes
--   WHERE v.scheme_id = s.scheme_id AND v.hard = 1
--     AND (
--       (v.op = 'in'     AND a.value NOT IN (SELECT je.value FROM json_each(v.value) je)) OR
--       (v.op = 'not_in' AND a.value     IN (SELECT je.value FROM json_each(v.value) je)) OR
--       (v.op = 'eq'     AND a.value <> v.value) OR
--       (v.op = 'gte'    AND a.ord  <  (SELECT ord FROM attr_values
--                                        WHERE attribute = v.attribute AND value = v.value)) OR
--       (v.op = 'lte'    AND a.ord  >  (SELECT ord FROM attr_values
--                                        WHERE attribute = v.attribute AND value = v.value))
--     )
-- )
--   AND (s.state_scope IS NULL
--        OR s.state_scope = (SELECT value FROM ans WHERE attribute = 'state'))
-- GROUP BY s.scheme_id
-- ORDER BY s.verified DESC, boost DESC, s.name;
