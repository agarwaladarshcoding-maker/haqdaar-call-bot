-- Haqdaar Voice - Scheme Catalogue v4
-- SUPERSEDED (v5, see CHANGE_REPORT.md #7): dial code here is
-- ps_key+slot_key+sec_key (a menu path) — CHANGE_REPORT calls this "a
-- mistake" because reordering a menu silently moves every code. ingest.py
-- replaces it with a stable scheme_no assigned in sorted-slug order. Do not
-- run this file against the DB; ingest.py is the live schema and loader.
-- Kept for the worked eligibility -> scheme_rules example in section 3.
--
-- Matches the myscheme JSON shape exactly, plus the THREE-LEVEL NUMBER TREE.
-- Load AFTER attribute_schema.sql (it replaces the old `schemes` table).

PRAGMA foreign_keys = ON;
DROP TABLE IF EXISTS schemes;

-- ===========================================================================
-- 1. RAW LAYER - one row per scheme, 1:1 with the source JSON.
--    Nothing is interpreted here. Ingest is lossless and re-runnable.
-- ===========================================================================
CREATE TABLE schemes (
  slug            TEXT PRIMARY KEY,   -- "ira-wrflsncs"
  scheme_name     TEXT NOT NULL,
  details         TEXT,
  benefits        TEXT,
  eligibility     TEXT,
  application     TEXT,
  documents       TEXT,
  level           TEXT CHECK (level IN ('Central','State')),

  -- derived at ingest, NOT in the source JSON:
  state_scope     TEXT,               -- NULL = all-India
  district_scope  TEXT,               -- NULL = whole state. See future/06.
  name_short_hi   TEXT,               -- <= 8 words. What we actually SAY.
  benefit_one_line TEXT,              -- <= 25 words. Spoken at PRESENT.
  verified        INTEGER NOT NULL DEFAULT 0
);

-- !! GAP IN THE SOURCE DATA !!
-- The sample has level = "State" but NO state field. "Puducherry" appears only
-- inside the `details` prose. `state` is the highest-value narrowing attribute
-- we have, so state_scope MUST be extracted at ingest by scanning
-- details + eligibility. If extraction fails, leave NULL (= never eliminated).
-- A wrong-state scheme leaking through is survivable. A correct scheme hidden
-- is not.

CREATE TABLE scheme_categories (
  slug     TEXT NOT NULL REFERENCES schemes(slug),
  category TEXT NOT NULL,
  PRIMARY KEY (slug, category)
);

CREATE TABLE scheme_tags (
  slug TEXT NOT NULL REFERENCES schemes(slug),
  tag  TEXT NOT NULL,
  PRIMARY KEY (slug, tag)
);

CREATE VIRTUAL TABLE schemes_fts USING fts5(
  scheme_name, details, benefits, eligibility,
  content='schemes', content_rowid='rowid'
);

-- ===========================================================================
-- 2. THE NUMBER TREE - three levels, never more than 6 options at any level
-- ===========================================================================
--
--   LEVEL 1              LEVEL 2                LEVEL 3
--   problem statement    scheme under it        section of that scheme
--   1..6                 1..5                   1..5
--
--   Caller path "2 3 1" = problem 2, scheme 3, "kya milega".
--   The same three digits are also a DIRECT DIAL code: type 231 at the main
--   menu and you land straight on that section. Same tree, one shortcut.
--
-- Why this shape: a caller can hold about 5 spoken options in memory. Three
-- levels of 5 addresses 125 schemes. We have 100. It fits with room to spare,
-- and every menu stays short enough to listen to.

CREATE TABLE problem_statements (
  ps_key    TEXT PRIMARY KEY CHECK (ps_key IN ('1','2','3','4','5','6')),
  label_hi  TEXT NOT NULL,
  label_en  TEXT NOT NULL,
  purpose   TEXT,                     -- maps to attr_values('purpose')
  ord       INTEGER
);

INSERT INTO problem_statements (ps_key, label_hi, label_en, purpose, ord) VALUES
('1','Paisa ya aamdani ki madad',   'Money or income support',   'income_support',    1),
('2','Karza ya loan',               'Loan or credit',            'credit_loan',       2),
('3','Fasal beema ya nuksaan',      'Crop insurance or damage',  'crop_insurance',    3),
('4','Yantra, sinchai ya subsidy',  'Equipment, irrigation, subsidy','equipment_subsidy',4),
('5','Pension, awas ya kalyan',     'Pension, housing, welfare', 'welfare',           5),
('6','Pashu, dairy ya matsya',      'Livestock, dairy, fishery', 'livestock_allied',  6);

-- Level 2: up to 5 schemes hang off each problem statement.
CREATE TABLE scheme_slots (
  ps_key      TEXT NOT NULL REFERENCES problem_statements(ps_key),
  slot_key    TEXT NOT NULL CHECK (slot_key IN ('1','2','3','4','5')),
  slug        TEXT NOT NULL REFERENCES schemes(slug),
  ord         INTEGER,
  PRIMARY KEY (ps_key, slot_key)
);

-- A scheme may legitimately appear under two problem statements.
CREATE INDEX idx_slots_slug ON scheme_slots(slug);

-- Level 3: the SAME five sections for every scheme in the system, so the
-- caller learns the menu once and we record the audio for it once.
CREATE TABLE detail_sections (
  sec_key   TEXT PRIMARY KEY CHECK (sec_key IN ('1','2','3','4','5')),
  column_name TEXT NOT NULL,
  label_hi  TEXT NOT NULL,
  label_en  TEXT NOT NULL,
  ord       INTEGER
);

INSERT INTO detail_sections (sec_key, column_name, label_hi, label_en, ord) VALUES
('1','benefits',    'kya milega',          'what you get',     1),
('2','eligibility', 'kaun le sakta hai',   'who is eligible',  2),
('3','documents',   'kaunse kaagaz chahiye','documents needed', 3),
('4','application', 'aavedan kaise kare',  'how to apply',     4),
('5','details',     'yojna ke baare mein', 'about the scheme', 5);

-- 0 main menu, * repeat, # back are GLOBAL. They are never listed in a menu
-- and never appear in these tables.

-- Convenience view: the full dial code for every scheme section.
CREATE VIEW dial_codes AS
SELECT
  ss.ps_key || ss.slot_key || ds.sec_key AS code,
  ss.ps_key, ss.slot_key, ds.sec_key,
  s.slug, s.name_short_hi, ds.label_hi AS section_hi
FROM scheme_slots ss
JOIN schemes s ON s.slug = ss.slug
CROSS JOIN detail_sections ds;

-- ===========================================================================
-- 3. WORKED EXAMPLE - the sample scheme, fully mapped. Pattern for all 100.
-- ===========================================================================
INSERT INTO schemes (slug, scheme_name, level, state_scope,
                     name_short_hi, benefit_one_line, verified) VALUES
('ira-wrflsncs',
 '"Immediate Relief Assistance" under "Welfare and Relief for Fishermen During Lean Seasons and Natural Calamities Scheme"',
 'State', 'PY',
 'Lapata machhuare ke parivaar ko turant sahayata',
 'Ek lakh rupaye, do kishton mein, lapata machhuare ke parivaar ko.',
 0);

-- Sits under problem statement 6 (livestock/fishery), slot 1. Dial code 611.
INSERT INTO scheme_slots (ps_key, slot_key, slug, ord) VALUES
('6','1','ira-wrflsncs',1);

INSERT INTO scheme_categories VALUES
('ira-wrflsncs','Agriculture'),
('ira-wrflsncs','Rural & Environment'),
('ira-wrflsncs','Social welfare & Empowerment');

INSERT INTO scheme_tags VALUES
('ira-wrflsncs','Missing'),
('ira-wrflsncs','Fisherman'),
('ira-wrflsncs','Relief'),
('ira-wrflsncs','Financial Assistance'),
('ira-wrflsncs','Family');

-- The eligibility PROSE becomes RULES. This step is the whole job.
INSERT INTO scheme_rules (scheme_id, attribute, op, value, hard, weight, source_quote) VALUES

-- HARD: stated as an absolute bar in the source text
('ira-wrflsncs','state','eq','PY',1,1.0,
  'The scheme is extended to all the regions of the Union territory of Puducherry.'),
('ira-wrflsncs','occupation','in','["fisher"]',1,1.0,
  'The applicant should be the family (legal heir) of the missing fisherman.'),
('ira-wrflsncs','age_band','gte','18_25',1,1.0,
  'The missing fisherman must have been in the age group of 18-60 years.'),
('ira-wrflsncs','age_band','lte','46_60',1,1.0,
  'The missing fisherman must have been in the age group of 18-60 years.'),

-- SOFT: a real signal, but not worth eliminating a caller over
('ira-wrflsncs','existing_pension','not_in','["old_age"]',0,0.8,
  'The missing fisherman must not have been a beneficiary of the old age pension scheme.'),
('ira-wrflsncs','coop_member','eq','yes',0,0.8,
  'The missing fisherman should have enrolled as a member of the Fishermen Co-operative Society.');

-- NOT MODELLED, deliberately:
--   "must have lost his/her life while fishing" -> a CLAIM condition, not a
--   narrowing attribute. We will never ask this on a phone. It stays in the
--   spoken eligibility text.
--   "apply within 30 days" -> deadline prose, and verified = 0, so the line
--   must NOT speak it as a deadline.

-- ===========================================================================
-- 4. INGEST CHECKLIST for the remaining 99
-- ===========================================================================
--  1. Insert the raw row verbatim. Never edit source prose.
--  2. Extract state_scope from `details`. NULL if unsure.
--  3. Write name_short_hi. The raw name is 20 words and unspeakable.
--  4. Write benefit_one_line, under 25 words.
--  5. Assign it a (ps_key, slot_key). Max 5 per problem statement.
--     ONCE ASSIGNED, NEVER RESHUFFLE. The code gets spoken aloud and written
--     on leaflets; changing it breaks every caller who noted one down.
--  6. Convert `eligibility` prose to scheme_rules. 3-6 rows is typical.
--     hard=1 ONLY when the text states an absolute bar. When in doubt, hard=0.
--  7. verified stays 0 unless a human checked it against the source.
--     Deadlines are spoken ONLY from verified=1 rows.
