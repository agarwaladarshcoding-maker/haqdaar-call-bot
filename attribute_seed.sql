-- Attribute dictionary seed v2 - matches question_bank.yaml v3, which was
-- rewritten against the real 100-scheme catalogue (optimized_schemes.json
-- via scripts/ingest.py), not the farmer-first v1/v2 assumption. See the
-- header of question_bank.yaml for the full rationale and CHANGE_REPORT.md
-- for the original v4->v5 correction this continues.
--
-- 33 real (non-session, non-scratch) attributes. Every question in
-- question_bank.yaml writes into exactly one of these. validate_bank.py
-- checks this file against the bank on every run.

INSERT INTO attributes (attribute, kind, label_en, notes) VALUES
('language','session','Call language','Session config, not eligibility'),
('intent','session','Why they called','Routes the whole call'),
('on_behalf','session','Calling for self or other',NULL),
('persona','enum','Broad caller identity','business / artisan / fisher / farmer / other'),
('applicant_type','enum','Business, person or either','CHANGE_REPORT #5: highest-gain question in the bank'),
('is_student','bool','Is a student',NULL),
('theme','enum','Scheme theme','Matches scripts/ingest.py THEMES exactly: business/craft/fisheries/training/farming/welfare'),

-- Business (LAYER 3). Grounded in real eligibility-text frequency: investment
-- ceilings and employment_created each hit 18/100 rows, enterprise_type and
-- unit_stage 14/100, is_registered_firm/udyam/loan_taken lower but real.
('enterprise_type','enum','Manufacturing, service or both',NULL),
('unit_stage','enum','New or existing unit',NULL),
('investment_size','band','Investment in plant/machinery or the unit',NULL),
('employment_created','band','People employed by the unit',NULL),
('is_registered_firm','bool','Enterprise is a registered firm/company/society',NULL),
('udyam_registered','bool','Udyam / MSME registered',NULL),
('loan_taken','bool','Bank loan taken for the business',NULL),

-- Craft and fisheries (LAYER 4). Both themes are dominated by individual-
-- craftsperson-or-society and boat/coop-membership eligibility text.
('applies_as','enum','Applying as individual or society',NULL),
('coop_member','bool','Member of a cooperative society',NULL),
('boat_owner','bool','Owns a fishing boat','Gates most fisheries-theme schemes'),
('craft_type','enum','Handloom, coir or other craft',NULL),
('is_literate','bool','Can read and write',NULL),
('years_in_trade_band','band','Years in the craft/fishing trade',NULL),

-- Training (LAYER 5). Real rows are trade-linked (coir, silk, industrial),
-- not generic youth skilling.
('training_related','bool','Wants training in a specific trade',NULL),
('currently_employed','bool','Currently employed in an industry',NULL),

-- Farming (LAYER 6). Only 6 schemes in the catalogue, all crop-specific.
('crop_grown','enum','Main crop','paddy/coconut/sugarcane/fodder/horticulture/other'),
('owns_land','bool','Owns agricultural land',NULL),

-- Welfare / demographic (LAYER 7). Welfare theme here is mostly worker-
-- registration and disability/relief schemes, not classic farmer pension.
('social_category','enum','Social category',NULL),
('gender','enum','Gender',NULL),
('age_band','band','Age band',NULL),
('income_band','band','Annual household income band',NULL),
('disability','bool','Person with disability',NULL),
('is_widow','bool','Widow',NULL),
('worker_registered','enum','Registered with a labour welfare board',NULL),
('education_need','bool','Scholarship / coaching need',NULL),
('household_size','band','Household size',NULL),
('existing_pension','bool','Already receiving a pension',NULL),

-- Geography (LAYER 8). DEMOTED per CHANGE_REPORT #3: 62/100 schemes are
-- Puducherry, 16/100 West Bengal. Late tiebreaker only, never a hard gate
-- on a NULL state_scope.
('state','enum','State / UT','Late tiebreaker, not an early filter - see CHANGE_REPORT #3'),

-- Finance (LAYER 9). Catalogue-agnostic DBT/document readiness.
('has_bank_account','bool','Bank account',NULL),
('aadhaar_linked','bool','Aadhaar linked to bank',NULL),
('has_aadhaar','bool','Has Aadhaar',NULL),
('has_income_cert','bool','Income certificate',NULL);

-- Banded values carry `ord` so gte/lte comparisons work in SQL.
INSERT INTO attr_values (attribute, value, label_en, ord) VALUES
('investment_size','lt25l','Under Rs 25 lakh',1),
('investment_size','25l_5cr','Rs 25 lakh to 5 crore',2),
('investment_size','gt5cr','Over Rs 5 crore',3),
('employment_created','none','No employees',1),
('employment_created','1_5','1 to 5',2),
('employment_created','gt5','More than 5',3),
('years_in_trade_band','lt3y','Under 3 years',1),
('years_in_trade_band','gte3y','3 years or more',2),
('income_band','lt1l','Under Rs 1 lakh',1),
('income_band','1l_2_5l','Rs 1 to 2.5 lakh',2),
('income_band','2_5l_5l','Rs 2.5 to 5 lakh',3),
('income_band','gt5l','Over Rs 5 lakh',4),
('age_band','lt18','Under 18',1),
('age_band','18_40','18 to 40',2),
('age_band','41_59','41 to 59',3),
('age_band','gte60','60 and above',4),
('household_size','1_2','1 to 2',1),
('household_size','3_5','3 to 5',2),
('household_size','gt5','More than 5',3);
