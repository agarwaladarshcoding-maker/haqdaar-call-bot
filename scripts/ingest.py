#!/usr/bin/env python3
"""Haqdaar ingest - builds haqdaar.db from optimized_schemes.json.

Idempotent: rebuilds from scratch each run. Safe to re-run any number of times.

Usage:  python3 ingest.py [source.json] [out.db]
"""
import json, re, sqlite3, sys, os, collections

SRC = sys.argv[1] if len(sys.argv) > 1 else '/data/optimized_schemes.json'
DB  = sys.argv[2] if len(sys.argv) > 2 else '/data/haqdaar.db'

STATES = {
 'Andhra Pradesh':'AP','Arunachal Pradesh':'AR','Assam':'AS','Bihar':'BR',
 'Chhattisgarh':'CG','Goa':'GA','Gujarat':'GJ','Haryana':'HR',
 'Himachal Pradesh':'HP','Jharkhand':'JH','Karnataka':'KA','Kerala':'KL',
 'Madhya Pradesh':'MP','Maharashtra':'MH','Manipur':'MN','Meghalaya':'ML',
 'Mizoram':'MZ','Nagaland':'NL','Odisha':'OD','Punjab':'PB','Rajasthan':'RJ',
 'Sikkim':'SK','Tamil Nadu':'TN','Telangana':'TS','Tripura':'TR',
 'Uttar Pradesh':'UP','Uttarakhand':'UK','West Bengal':'WB','Puducherry':'PY',
 'Delhi':'DL','Jammu and Kashmir':'JK','Ladakh':'LA','Chandigarh':'CH',
 'Lakshadweep':'LD',
}

# LEVEL 1 menu - derived from the real catalogue, ordered by actual volume so
# the commonest ask is option 1.
THEMES = [
 ('1','business',  'Vyapar, udyog ya dukaan',  'Business, industry or shop',
   ['msme','industry','entrepreneur','enterprise','startup','industrial',
    'manufactur','business','investment','capital subsidy','plant and machinery',
    'incentive','unit','tender','export']),
 ('2','craft',     'Bunkar, coir ya dastkari', 'Handloom, coir or handicraft',
   ['coir','handicraft','handloom','artisan','weaver','khadi','textile',
    'craft','powerloom','spinner','silk']),
 ('3','fisheries', 'Machhli palan ya nauka',   'Fisheries or boats',
   ['fisher','fish','boat','marine','aqua','catamaran','vallam','net','trawler']),
 ('4','training',  'Prashikshan ya rozgar',    'Training or employment',
   ['skill','training','stipend','apprentice','employment','placement',
    'internship','trainee','coaching']),
 ('5','farming',   'Kheti ya baagwani',        'Farming or horticulture',
   ['farmer','crop','agricultur','horticultur','irrigation','soil','seed',
    'paddy','plantation','fodder','greenhouse','cattle','dairy']),
 ('6','welfare',   'Pension, madad ya padhai', 'Pension, relief or study',
   ['pension','widow','disabil','old age','destitute','orphan','relief',
    'calamity','death','funeral','scholarship','student','treatment',
    'medical','shramik','marriage','burial']),
]

# LEVEL 2 menu - only the business theme needs it. Split by WHAT THE CALLER
# NEEDS, not by industry sector: a shop owner knows the electricity bill hurts,
# but may not know whether they count as "MSME manufacturing".
NEEDS = [
 ('1','machine_setup','Machine, aujaar ya jagah',    'Machinery, tools or premises',
   ['capital investment subsidy','plant and machinery','purchase of machinery',
    'equipment','machinery','tools','capital subsidy','fixed capital',
    'infrastructure','shed','plot','industrial estate','building']),
 ('2','loan_interest','Karza ya byaj mein chhoot',   'Loan or interest relief',
   ['interest subsidy','term loan','interest on','loan','credit','collateral']),
 ('3','tax_waiver',  'Tax, stamp duty ya wapsi',     'Tax, stamp duty or refund',
   ['stamp duty','waiver','reimbursement','vat','gst','tax','registration fee',
    'conversion charges','electricity duty']),
 ('4','power_water', 'Bijli ya paani',               'Electricity or water',
   ['electricity','power tariff','power subsidy','energy','water','effluent',
    'generator','captive power']),
 ('5','market_sale', 'Bikri, mela ya export',        'Marketing, fairs or export',
   ['marketing','exhibition','trade fair','export','consortia','tender',
    'brand','publicity','stall']),
 ('6','tech_quality','Technology, patent ya quality','Technology, patent or quality',
   ['quality certification','iso','patent','technology','innovation','r&d',
    'testing','standardisation','intellectual property']),
]

SECTIONS = [
 ('1','benefits',   'kya milega',            'what you get'),
 ('2','eligibility','kaun le sakta hai',     'who is eligible'),
 ('3','documents',  'kaunse kaagaz chahiye', 'documents needed'),
 ('4','application','aavedan kaise kare',    'how to apply'),
 ('5','details',    'yojna ke baare mein',   'about the scheme'),
]

SCHEMA = """
CREATE TABLE schemes (
  slug            TEXT PRIMARY KEY,
  scheme_no       INTEGER UNIQUE NOT NULL,
  scheme_name     TEXT NOT NULL,
  details TEXT, benefits TEXT, eligibility TEXT,
  application TEXT, documents TEXT,
  level           TEXT,
  state_scope     TEXT,
  district_scope  TEXT,
  theme           TEXT NOT NULL,
  need_group      TEXT,
  applicant_type  TEXT,
  name_short_hi   TEXT,
  benefit_one_line TEXT,
  needs_human_name INTEGER DEFAULT 0,
  verified        INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE scheme_categories (slug TEXT, category TEXT, PRIMARY KEY (slug, category));
CREATE TABLE scheme_tags       (slug TEXT, tag TEXT,      PRIMARY KEY (slug, tag));
CREATE TABLE problem_statements (ps_key TEXT PRIMARY KEY, theme TEXT, label_hi TEXT, label_en TEXT, ord INTEGER);
CREATE TABLE need_groups (need_key TEXT PRIMARY KEY, need_group TEXT, label_hi TEXT, label_en TEXT, ord INTEGER);
CREATE TABLE detail_sections (sec_key TEXT PRIMARY KEY, column_name TEXT, label_hi TEXT, label_en TEXT, ord INTEGER);
CREATE TABLE scheme_rules (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  scheme_id TEXT NOT NULL, attribute TEXT NOT NULL, op TEXT NOT NULL,
  value TEXT, hard INTEGER NOT NULL DEFAULT 0, weight REAL DEFAULT 1.0,
  source_quote TEXT);
CREATE INDEX idx_rules_scheme ON scheme_rules(scheme_id);
CREATE INDEX idx_rules_attr   ON scheme_rules(attribute);
CREATE INDEX idx_schemes_theme ON schemes(theme, need_group);

-- attribute dictionary (attribute_seed.sql loads into these). ord on
-- attr_values is what narrow.py uses for gte/lte band comparisons.
CREATE TABLE attributes (
  attribute TEXT PRIMARY KEY, kind TEXT NOT NULL, label_en TEXT, notes TEXT);
CREATE TABLE attr_values (
  attribute TEXT NOT NULL, value TEXT NOT NULL, label_en TEXT, ord INTEGER,
  PRIMARY KEY (attribute, value));
"""

# attribute_seed.sql lives at the repo root, one level up from scripts/.
ATTR_SEED_PATH = os.path.join(os.path.dirname(__file__), "..", "attribute_seed.sql")


def extract_state(s):
    blob = ' '.join([s.get('scheme_name') or '', s.get('details') or '',
                     s.get('eligibility') or ''])
    hits = {c for n, c in STATES.items()
            if re.search(r'\b' + re.escape(n) + r'\b', blob, re.I)}
    # exactly one match wins; zero or many leaves NULL, which never eliminates
    return hits.pop() if len(hits) == 1 else None


def best_bucket(blob, table):
    best, best_n = None, 0
    for key, name, _, _, kws in table:
        n = sum(blob.count(k) for k in kws)
        if n > best_n:
            best, best_n = name, n
    return best, best_n


def classify_theme(s):
    blob = ' '.join([s.get('scheme_name') or '', ' '.join(s.get('tags') or []),
                     ' '.join(s.get('schemeCategory') or []),
                     (s.get('details') or '')[:800]]).lower()
    return best_bucket(blob, THEMES)[0] or 'business'


def classify_need(s):
    blob = ' '.join([s.get('scheme_name') or '', (s.get('benefits') or '')[:600],
                     (s.get('details') or '')[:400],
                     ' '.join(s.get('tags') or [])]).lower()
    return best_bucket(blob, NEEDS)[0] or 'machine_setup'


def applicant_type(s):
    e = (s.get('eligibility') or '').lower()
    biz = any(w in e for w in ['unit','enterprise','industry','firm','company',
                               'entrepreneur','msme','society','proprietor'])
    per = any(w in e for w in ['applicant should be','beneficiary','person',
                               'candidate','age of','resident','student','worker'])
    if biz and per: return 'both'
    if biz: return 'business'
    if per: return 'person'
    return 'both'


def short_name(s):
    """Best-effort speakable name. Anything still long is flagged for a human."""
    n = s['scheme_name']
    m = re.match(r'\s*["\u201c]([^"\u201d]{3,})["\u201d]', n)
    if m:
        n = m.group(1)
    n = re.split(r'\s+under\s+|\s+component of\s+|\s+by\s+the\s+|:\s*', n,
                 flags=re.I)[0]
    n = n.strip(' "\u201c\u201d')
    words = n.split()
    return ' '.join(words[:8]), len(words) > 8


def main():
    data = json.load(open(SRC, encoding='utf-8'))
    if os.path.exists(DB):
        os.remove(DB)
    db = sqlite3.connect(DB)
    db.executescript(SCHEMA)
    if os.path.exists(ATTR_SEED_PATH):
        db.executescript(open(ATTR_SEED_PATH, encoding='utf-8').read())

    db.executemany('INSERT INTO detail_sections VALUES (?,?,?,?,?)',
        [(k, c, hi, en, i+1) for i, (k, c, hi, en) in enumerate(SECTIONS)])
    db.executemany('INSERT INTO problem_statements VALUES (?,?,?,?,?)',
        [(k, t, hi, en, i+1) for i, (k, t, hi, en, _) in enumerate(THEMES)])
    db.executemany('INSERT INTO need_groups VALUES (?,?,?,?,?)',
        [(k, t, hi, en, i+1) for i, (k, t, hi, en, _) in enumerate(NEEDS)])

    stats = collections.Counter()

    # scheme_no is assigned in a DETERMINISTIC order (sorted by slug) so a
    # re-run never renumbers anything. Dial codes get spoken aloud and written
    # down; silently renumbering betrays every caller who noted one.
    for i, s in enumerate(sorted(data, key=lambda x: x['slug']), start=1):
        st = extract_state(s)
        th = classify_theme(s)
        ng = classify_need(s) if th == 'business' else None
        at = applicant_type(s)
        sn, flag = short_name(s)

        stats['state_ok' if st else 'state_null'] += 1
        if flag: stats['name_needs_human'] += 1

        db.execute("""INSERT INTO schemes
          (slug,scheme_no,scheme_name,details,benefits,eligibility,application,
           documents,level,state_scope,district_scope,theme,need_group,
           applicant_type,name_short_hi,benefit_one_line,needs_human_name,verified)
          VALUES (?,?,?,?,?,?,?,?,?,?,NULL,?,?,?,?,NULL,?,0)""",
          (s['slug'], i, s['scheme_name'], s.get('details'), s.get('benefits'),
           s.get('eligibility'), s.get('application'), s.get('documents'),
           s.get('level'), st, th, ng, at, sn, 1 if flag else 0))

        for c in s.get('schemeCategory') or []:
            db.execute('INSERT OR IGNORE INTO scheme_categories VALUES (?,?)',
                       (s['slug'], c))
        for t in s.get('tags') or []:
            db.execute('INSERT OR IGNORE INTO scheme_tags VALUES (?,?)',
                       (s['slug'], t))

    db.commit()
    report(db, stats)


def report(db, stats):
    q = lambda sql, *a: db.execute(sql, a).fetchall()
    bar = '=' * 64
    print(bar); print('HAQDAAR INGEST COMPLETE ->', DB); print(bar)
    print('schemes loaded       :', q('select count(*) from schemes')[0][0])
    print('state resolved       :', stats['state_ok'], '/ 100')
    print('state NULL (safe)    :', stats['state_null'])
    print('names needing human  :', stats['name_needs_human'], '/ 100')
    print()
    print('LEVEL 1 MENU')
    for k, t, hi, _, _ in THEMES:
        print('  %s  %-28s %3d' % (k, hi,
              q('select count(*) from schemes where theme=?', t)[0][0]))
    print()
    print('LEVEL 2 MENU (business only)')
    for k, t, hi, _, _ in NEEDS:
        print('  %s  %-28s %3d' % (k, hi,
              q('select count(*) from schemes where need_group=?', t)[0][0]))
    print()
    print('APPLICANT TYPE')
    for r in q('select applicant_type, count(*) from schemes group by 1 order by 2 desc'):
        print('  %-10s %d' % r)
    print()
    print('STATE SPREAD')
    for r in q('''select coalesce(state_scope,'NULL'), count(*) from schemes
                  group by 1 order by 2 desc limit 6'''):
        print('  %-6s %d' % r)
    print()
    print('SAMPLE DIAL CODES  (2-digit scheme + 1-digit section)')
    for r in q('select scheme_no, name_short_hi from schemes order by scheme_no limit 4'):
        print('  %02d1 -> "%s" / kya milega' % (r[0], (r[1] or '')[:42]))


if __name__ == '__main__':
    main()
