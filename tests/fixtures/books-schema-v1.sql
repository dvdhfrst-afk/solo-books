BEGIN TRANSACTION;
CREATE TABLE accounts (
        id                     INTEGER PRIMARY KEY,
        business_id            INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
        name                   TEXT NOT NULL UNIQUE COLLATE NOCASE,
        kind                   TEXT NOT NULL CHECK (kind IN ('checking', 'savings', 'credit_card', 'other')),
        institution            TEXT NOT NULL DEFAULT '',
        opening_balance_cents  INTEGER CHECK (opening_balance_cents IS NULL
                                              OR typeof(opening_balance_cents) = 'integer'),
        opening_date           TEXT,
        mapping_json           TEXT,
        created_at             TEXT NOT NULL DEFAULT (datetime('now'))
    );
INSERT INTO "accounts" VALUES(1,1,'A Checking','checking','Example Bank',500000,'2025-01-01','{
 "date_column": "Date",
 "date_format": "%m/%d/%Y",
 "description_column": "Description",
 "amount_column": "Amount",
 "debit_column": null,
 "credit_column": null,
 "invert_amount": false,
 "memo_column": null,
 "reference_column": null,
 "status_column": null,
 "skip_statuses": [
  "pending"
 ],
 "skip_lines": 0,
 "delimiter": ",",
 "encoding": null
}','2026-11-07 16:57:39');
INSERT INTO "accounts" VALUES(2,1,'A Card','credit_card','Example Card',-20000,'2025-01-01','{
 "date_column": "Post Date",
 "date_format": "%m/%d/%Y",
 "description_column": "Description",
 "amount_column": "Amount",
 "debit_column": null,
 "credit_column": null,
 "invert_amount": false,
 "memo_column": null,
 "reference_column": null,
 "status_column": null,
 "skip_statuses": [
  "pending"
 ],
 "skip_lines": 0,
 "delimiter": ",",
 "encoding": null
}','2026-11-07 16:57:39');
INSERT INTO "accounts" VALUES(3,2,'B Checking','checking','',100000,'2025-01-01','{
 "date_column": "Date",
 "date_format": "%m/%d/%Y",
 "description_column": "Description",
 "amount_column": null,
 "debit_column": "Debit",
 "credit_column": "Credit",
 "invert_amount": false,
 "memo_column": null,
 "reference_column": null,
 "status_column": "Status",
 "skip_statuses": [
  "pending"
 ],
 "skip_lines": 2,
 "delimiter": ",",
 "encoding": null
}','2026-11-07 16:57:40');
CREATE TABLE businesses (
        id          INTEGER PRIMARY KEY,
        name        TEXT NOT NULL UNIQUE COLLATE NOCASE,
        activity    TEXT NOT NULL DEFAULT '',
        created_at  TEXT NOT NULL DEFAULT (datetime('now'))
    );
INSERT INTO "businesses" VALUES(1,'Business A','Consulting','2026-11-07 16:57:38');
INSERT INTO "businesses" VALUES(2,'Business B','Online shop','2026-11-07 16:57:38');
CREATE TABLE categories (
        id              INTEGER PRIMARY KEY,
        business_id     INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
        code            TEXT NOT NULL,
        name            TEXT NOT NULL COLLATE NOCASE,
        kind            TEXT NOT NULL CHECK (kind IN
                            ('income', 'returns', 'cogs', 'expense', 'transfer', 'equity')),
        schedule_c_key  TEXT,
        active          INTEGER NOT NULL DEFAULT 1,
        UNIQUE (business_id, code),
        UNIQUE (business_id, name)
    );
INSERT INTO "categories" VALUES(1,1,'3000','Owner contributions','equity',NULL,1);
INSERT INTO "categories" VALUES(2,1,'3100','Owner draws','equity',NULL,1);
INSERT INTO "categories" VALUES(3,1,'3900','Transfer','transfer',NULL,1);
INSERT INTO "categories" VALUES(4,1,'4000','Sales','income','gross_receipts',1);
INSERT INTO "categories" VALUES(5,1,'4100','Returns and allowances','returns','returns_allowances',1);
INSERT INTO "categories" VALUES(6,1,'4900','Other income','income','other_income',1);
INSERT INTO "categories" VALUES(7,1,'5000','Purchases for resale','cogs','cogs_purchases',1);
INSERT INTO "categories" VALUES(8,1,'5100','Direct labor','cogs','cogs_labor',1);
INSERT INTO "categories" VALUES(9,1,'5200','Materials','cogs','cogs_materials',1);
INSERT INTO "categories" VALUES(10,1,'5300','Other direct costs','cogs','cogs_other',1);
INSERT INTO "categories" VALUES(11,1,'6000','Advertising','expense','advertising',1);
INSERT INTO "categories" VALUES(12,1,'6010','Car and truck','expense','car_truck',1);
INSERT INTO "categories" VALUES(13,1,'6020','Commissions and fees','expense','commissions_fees',1);
INSERT INTO "categories" VALUES(14,1,'6030','Contract labor','expense','contract_labor',1);
INSERT INTO "categories" VALUES(15,1,'6040','Employee benefits','expense','employee_benefits',1);
INSERT INTO "categories" VALUES(16,1,'6050','Business insurance','expense','insurance',1);
INSERT INTO "categories" VALUES(17,1,'6060','Mortgage interest','expense','interest_mortgage',1);
INSERT INTO "categories" VALUES(18,1,'6063','Vehicle loan interest','expense','interest_vehicle_loan',1);
INSERT INTO "categories" VALUES(19,1,'6065','Other interest','expense','interest_other',1);
INSERT INTO "categories" VALUES(20,1,'6070','Legal and professional','expense','legal_professional',1);
INSERT INTO "categories" VALUES(21,1,'6080','Office expense','expense','office',1);
INSERT INTO "categories" VALUES(22,1,'6090','Retirement plans','expense','pension',1);
INSERT INTO "categories" VALUES(23,1,'6100','Equipment rent','expense','rent_equipment',1);
INSERT INTO "categories" VALUES(24,1,'6105','Office rent','expense','rent_property',1);
INSERT INTO "categories" VALUES(25,1,'6110','Repairs and maintenance','expense','repairs',1);
INSERT INTO "categories" VALUES(26,1,'6120','Supplies','expense','supplies',1);
INSERT INTO "categories" VALUES(27,1,'6130','Taxes and licenses','expense','taxes_licenses',1);
INSERT INTO "categories" VALUES(28,1,'6140','Travel','expense','travel',1);
INSERT INTO "categories" VALUES(29,1,'6150','Meals','expense','meals',1);
INSERT INTO "categories" VALUES(30,1,'6160','Utilities','expense','utilities',1);
INSERT INTO "categories" VALUES(31,1,'6170','Wages','expense','wages',1);
INSERT INTO "categories" VALUES(32,1,'6200','Software subscriptions','expense','other_expenses',1);
INSERT INTO "categories" VALUES(33,1,'6210','Bank fees','expense','other_expenses',1);
INSERT INTO "categories" VALUES(34,1,'6220','Education and training','expense','other_expenses',1);
INSERT INTO "categories" VALUES(35,1,'6290','Other expenses','expense','other_expenses',1);
INSERT INTO "categories" VALUES(36,2,'3000','Owner contributions','equity',NULL,1);
INSERT INTO "categories" VALUES(37,2,'3100','Owner draws','equity',NULL,1);
INSERT INTO "categories" VALUES(38,2,'3900','Transfer','transfer',NULL,1);
INSERT INTO "categories" VALUES(39,2,'4000','Sales','income','gross_receipts',1);
INSERT INTO "categories" VALUES(40,2,'4100','Returns and allowances','returns','returns_allowances',1);
INSERT INTO "categories" VALUES(41,2,'4900','Other income','income','other_income',1);
INSERT INTO "categories" VALUES(42,2,'5000','Purchases for resale','cogs','cogs_purchases',1);
INSERT INTO "categories" VALUES(43,2,'5100','Direct labor','cogs','cogs_labor',1);
INSERT INTO "categories" VALUES(44,2,'5200','Materials','cogs','cogs_materials',1);
INSERT INTO "categories" VALUES(45,2,'5300','Other direct costs','cogs','cogs_other',1);
INSERT INTO "categories" VALUES(46,2,'6000','Advertising','expense','advertising',1);
INSERT INTO "categories" VALUES(47,2,'6010','Car and truck','expense','car_truck',1);
INSERT INTO "categories" VALUES(48,2,'6020','Commissions and fees','expense','commissions_fees',1);
INSERT INTO "categories" VALUES(49,2,'6030','Contract labor','expense','contract_labor',1);
INSERT INTO "categories" VALUES(50,2,'6040','Employee benefits','expense','employee_benefits',1);
INSERT INTO "categories" VALUES(51,2,'6050','Business insurance','expense','insurance',1);
INSERT INTO "categories" VALUES(52,2,'6060','Mortgage interest','expense','interest_mortgage',1);
INSERT INTO "categories" VALUES(53,2,'6063','Vehicle loan interest','expense','interest_vehicle_loan',1);
INSERT INTO "categories" VALUES(54,2,'6065','Other interest','expense','interest_other',1);
INSERT INTO "categories" VALUES(55,2,'6070','Legal and professional','expense','legal_professional',1);
INSERT INTO "categories" VALUES(56,2,'6080','Office expense','expense','office',1);
INSERT INTO "categories" VALUES(57,2,'6090','Retirement plans','expense','pension',1);
INSERT INTO "categories" VALUES(58,2,'6100','Equipment rent','expense','rent_equipment',1);
INSERT INTO "categories" VALUES(59,2,'6105','Office rent','expense','rent_property',1);
INSERT INTO "categories" VALUES(60,2,'6110','Repairs and maintenance','expense','repairs',1);
INSERT INTO "categories" VALUES(61,2,'6120','Supplies','expense','supplies',1);
INSERT INTO "categories" VALUES(62,2,'6130','Taxes and licenses','expense','taxes_licenses',1);
INSERT INTO "categories" VALUES(63,2,'6140','Travel','expense','travel',1);
INSERT INTO "categories" VALUES(64,2,'6150','Meals','expense','meals',1);
INSERT INTO "categories" VALUES(65,2,'6160','Utilities','expense','utilities',1);
INSERT INTO "categories" VALUES(66,2,'6170','Wages','expense','wages',1);
INSERT INTO "categories" VALUES(67,2,'6200','Software subscriptions','expense','other_expenses',1);
INSERT INTO "categories" VALUES(68,2,'6210','Bank fees','expense','other_expenses',1);
INSERT INTO "categories" VALUES(69,2,'6220','Education and training','expense','other_expenses',1);
INSERT INTO "categories" VALUES(70,2,'6290','Other expenses','expense','other_expenses',1);
CREATE TABLE deleted_rows (
        account_id  INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
        dedupe_key  TEXT NOT NULL,
        natural_key TEXT NOT NULL,
        occurrence  INTEGER NOT NULL,
        deleted_at  TEXT NOT NULL DEFAULT (datetime('now')),
        PRIMARY KEY (account_id, dedupe_key, occurrence)
    );
CREATE TABLE import_rows (
        import_id       INTEGER NOT NULL REFERENCES imports(id) ON DELETE CASCADE,
        transaction_id  INTEGER NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
        PRIMARY KEY (import_id, transaction_id)
    );
INSERT INTO "import_rows" VALUES(1,1);
INSERT INTO "import_rows" VALUES(1,2);
INSERT INTO "import_rows" VALUES(1,3);
INSERT INTO "import_rows" VALUES(1,4);
INSERT INTO "import_rows" VALUES(1,5);
INSERT INTO "import_rows" VALUES(1,6);
INSERT INTO "import_rows" VALUES(1,7);
INSERT INTO "import_rows" VALUES(1,8);
INSERT INTO "import_rows" VALUES(2,5);
INSERT INTO "import_rows" VALUES(2,6);
INSERT INTO "import_rows" VALUES(2,7);
INSERT INTO "import_rows" VALUES(2,8);
INSERT INTO "import_rows" VALUES(2,9);
INSERT INTO "import_rows" VALUES(2,10);
INSERT INTO "import_rows" VALUES(2,11);
INSERT INTO "import_rows" VALUES(2,12);
INSERT INTO "import_rows" VALUES(2,13);
INSERT INTO "import_rows" VALUES(2,14);
INSERT INTO "import_rows" VALUES(3,15);
INSERT INTO "import_rows" VALUES(3,16);
INSERT INTO "import_rows" VALUES(3,17);
INSERT INTO "import_rows" VALUES(3,18);
INSERT INTO "import_rows" VALUES(3,19);
INSERT INTO "import_rows" VALUES(3,20);
INSERT INTO "import_rows" VALUES(3,21);
INSERT INTO "import_rows" VALUES(3,22);
INSERT INTO "import_rows" VALUES(3,23);
INSERT INTO "import_rows" VALUES(3,24);
INSERT INTO "import_rows" VALUES(3,25);
INSERT INTO "import_rows" VALUES(3,26);
INSERT INTO "import_rows" VALUES(3,27);
INSERT INTO "import_rows" VALUES(4,28);
INSERT INTO "import_rows" VALUES(4,29);
INSERT INTO "import_rows" VALUES(4,30);
INSERT INTO "import_rows" VALUES(4,31);
INSERT INTO "import_rows" VALUES(4,32);
INSERT INTO "import_rows" VALUES(4,33);
INSERT INTO "import_rows" VALUES(4,34);
INSERT INTO "import_rows" VALUES(4,35);
INSERT INTO "import_rows" VALUES(4,36);
CREATE TABLE imports (
        id              INTEGER PRIMARY KEY,
        account_id      INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
        file_name       TEXT NOT NULL,
        file_sha256     TEXT NOT NULL,
        imported_at     TEXT NOT NULL DEFAULT (datetime('now')),
        rows_read       INTEGER NOT NULL,
        rows_new        INTEGER NOT NULL,
        rows_duplicate  INTEGER NOT NULL,
        rows_skipped    INTEGER NOT NULL,
        mapping_json    TEXT NOT NULL,
        undone_at       TEXT
    );
INSERT INTO "imports" VALUES(1,1,'a_checking_jan_feb.csv','f78d094c5dca27a1f2c9f861f5b9be36d1878ebe1b9c29470d88264933739681','2026-11-07 16:57:41',8,8,0,0,'{
 "date_column": "Date",
 "date_format": "%m/%d/%Y",
 "description_column": "Description",
 "amount_column": "Amount",
 "debit_column": null,
 "credit_column": null,
 "invert_amount": false,
 "memo_column": null,
 "reference_column": null,
 "status_column": null,
 "skip_statuses": [
  "pending"
 ],
 "skip_lines": 0,
 "delimiter": ",",
 "encoding": null
}',NULL);
INSERT INTO "imports" VALUES(2,1,'a_checking_feb_mar.csv','8a1d9807bf02e18d80f6128a39dbceb06ee0565c026154a1bf5544e14526223d','2026-11-07 16:57:41',10,6,4,0,'{
 "date_column": "Date",
 "date_format": "%m/%d/%Y",
 "description_column": "Description",
 "amount_column": "Amount",
 "debit_column": null,
 "credit_column": null,
 "invert_amount": false,
 "memo_column": null,
 "reference_column": null,
 "status_column": null,
 "skip_statuses": [
  "pending"
 ],
 "skip_lines": 0,
 "delimiter": ",",
 "encoding": null
}',NULL);
INSERT INTO "imports" VALUES(3,2,'a_card_q1.csv','0f5bc8e18185b194a03b1362e359d19b41a77075e2a4f209836914079ed35b8b','2026-11-07 16:57:42',13,13,0,0,'{
 "date_column": "Post Date",
 "date_format": "%m/%d/%Y",
 "description_column": "Description",
 "amount_column": "Amount",
 "debit_column": null,
 "credit_column": null,
 "invert_amount": false,
 "memo_column": null,
 "reference_column": null,
 "status_column": null,
 "skip_statuses": [
  "pending"
 ],
 "skip_lines": 0,
 "delimiter": ",",
 "encoding": null
}',NULL);
INSERT INTO "imports" VALUES(4,3,'b_checking_q1.csv','ee8c1b74e1c4ee4a8fefcaddb4e3209d96edf00c9284712ceff8fdcb5c225064','2026-11-07 16:57:43',11,9,0,2,'{
 "date_column": "Date",
 "date_format": "%m/%d/%Y",
 "description_column": "Description",
 "amount_column": null,
 "debit_column": "Debit",
 "credit_column": "Credit",
 "invert_amount": false,
 "memo_column": null,
 "reference_column": null,
 "status_column": "Status",
 "skip_statuses": [
  "pending"
 ],
 "skip_lines": 2,
 "delimiter": ",",
 "encoding": null
}',NULL);
CREATE TABLE inventory_counts (
        business_id      INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
        tax_year         INTEGER NOT NULL,
        beginning_cents  INTEGER NOT NULL CHECK (typeof(beginning_cents) = 'integer'),
        ending_cents     INTEGER NOT NULL CHECK (typeof(ending_cents) = 'integer'),
        PRIMARY KEY (business_id, tax_year)
    );
INSERT INTO "inventory_counts" VALUES(2,2025,50000,75000);
CREATE TABLE reconciliations (
        id                     INTEGER PRIMARY KEY,
        account_id             INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
        period_start           TEXT NOT NULL,
        period_end             TEXT NOT NULL,
        statement_begin_cents  INTEGER NOT NULL CHECK (typeof(statement_begin_cents) = 'integer'),
        statement_end_cents    INTEGER NOT NULL CHECK (typeof(statement_end_cents) = 'integer'),
        status                 TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'finished')),
        created_at             TEXT NOT NULL DEFAULT (datetime('now')),
        finished_at            TEXT,
        UNIQUE (account_id, period_start, period_end)
    );
CREATE TABLE rule_exceptions (
        rule_id         INTEGER NOT NULL REFERENCES rules(id) ON DELETE CASCADE,
        transaction_id  INTEGER NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
        PRIMARY KEY (rule_id, transaction_id)
    );
CREATE TABLE rules (
        id           INTEGER PRIMARY KEY,
        business_id  INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
        account_id   INTEGER REFERENCES accounts(id) ON DELETE CASCADE,
        priority     INTEGER NOT NULL DEFAULT 100,
        match_type   TEXT NOT NULL CHECK (match_type IN ('contains', 'starts', 'exact', 'regex')),
        pattern      TEXT NOT NULL,
        direction    TEXT CHECK (direction IN ('in', 'out')),
        min_cents    INTEGER,
        max_cents    INTEGER,
        category_id  INTEGER NOT NULL REFERENCES categories(id),
        created_at   TEXT NOT NULL DEFAULT (datetime('now'))
    );
INSERT INTO "rules" VALUES(1,1,NULL,100,'starts','CLIENT ','in',NULL,NULL,4,'2026-11-07 16:57:40');
INSERT INTO "rules" VALUES(2,1,NULL,100,'contains','REDWOOD PLAZA','out',NULL,NULL,24,'2026-11-07 16:57:40');
INSERT INTO "rules" VALUES(3,1,NULL,100,'contains','SERVICE FEE',NULL,NULL,NULL,33,'2026-11-07 16:57:40');
INSERT INTO "rules" VALUES(4,1,NULL,100,'contains','LICENSE',NULL,NULL,NULL,27,'2026-11-07 16:57:40');
INSERT INTO "rules" VALUES(5,1,NULL,100,'contains','NOTION',NULL,NULL,NULL,32,'2026-11-07 16:57:40');
INSERT INTO "rules" VALUES(6,1,NULL,100,'contains','SKYWAY AIR',NULL,NULL,NULL,28,'2026-11-07 16:57:40');
INSERT INTO "rules" VALUES(7,1,NULL,100,'contains','HILLSIDE HOTEL',NULL,NULL,NULL,28,'2026-11-07 16:57:40');
INSERT INTO "rules" VALUES(8,1,NULL,100,'contains','STAPLE HUT',NULL,NULL,NULL,21,'2026-11-07 16:57:40');
INSERT INTO "rules" VALUES(9,1,NULL,100,'regex','^NEON\s+ADS$',NULL,NULL,NULL,11,'2026-11-07 16:57:40');
INSERT INTO "rules" VALUES(10,1,NULL,100,'exact','INTEREST PAID','in',NULL,NULL,6,'2026-11-07 16:57:40');
INSERT INTO "rules" VALUES(11,2,NULL,100,'contains','CUSTOMER REFUND',NULL,NULL,NULL,40,'2026-11-07 16:57:40');
INSERT INTO "rules" VALUES(12,2,NULL,200,'contains','SHOPFRONT',NULL,NULL,NULL,39,'2026-11-07 16:57:40');
INSERT INTO "rules" VALUES(13,2,NULL,100,'contains','WHOLESALE WIDGETS',NULL,NULL,NULL,42,'2026-11-07 16:57:40');
INSERT INTO "rules" VALUES(14,2,NULL,100,'contains','PACKWELL',NULL,NULL,NULL,44,'2026-11-07 16:57:40');
INSERT INTO "rules" VALUES(15,2,NULL,100,'contains','SHIPEASY',NULL,NULL,NULL,45,'2026-11-07 16:57:40');
INSERT INTO "rules" VALUES(16,2,NULL,100,'contains','HOSTLY',NULL,NULL,NULL,67,'2026-11-07 16:57:40');
CREATE TABLE transactions (
        id                 INTEGER PRIMARY KEY,
        account_id         INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
        posted_on          TEXT NOT NULL,
        amount_cents       INTEGER NOT NULL CHECK (typeof(amount_cents) = 'integer'),
        description        TEXT NOT NULL,
        memo               TEXT NOT NULL DEFAULT '',
        reference          TEXT NOT NULL DEFAULT '',
        merchant           TEXT NOT NULL,
        dedupe_key         TEXT NOT NULL,
        natural_key        TEXT NOT NULL,
        occurrence         INTEGER NOT NULL,
        import_id          INTEGER REFERENCES imports(id) ON DELETE SET NULL,
        category_id        INTEGER REFERENCES categories(id),
        category_source    TEXT CHECK (category_source IN ('rule', 'manual')),
        rule_id            INTEGER REFERENCES rules(id) ON DELETE SET NULL,
        reconciliation_id  INTEGER REFERENCES reconciliations(id) ON DELETE SET NULL,
        note               TEXT NOT NULL DEFAULT '',
        created_at         TEXT NOT NULL DEFAULT (datetime('now')),
        UNIQUE (account_id, dedupe_key, occurrence)
    );
INSERT INTO "transactions" VALUES(1,1,'2025-01-03',425000,'CLIENT NORTHWIND INVOICE 1001','','','CLIENT NORTHWIND INVOICE','row:2025-01-03|425000|CLIENT NORTHWIND INVOICE 1001','2025-01-03|425000|CLIENT NORTHWIND INVOICE 1001',1,1,4,'rule',1,NULL,'','2026-11-07 16:57:41');
INSERT INTO "transactions" VALUES(2,1,'2025-01-06',-85000,'REDWOOD PLAZA RENT','','','REDWOOD PLAZA RENT','row:2025-01-06|-85000|REDWOOD PLAZA RENT','2025-01-06|-85000|REDWOOD PLAZA RENT',1,1,24,'rule',2,NULL,'','2026-11-07 16:57:41');
INSERT INTO "transactions" VALUES(3,1,'2025-01-15',-20000,'EXAMPLE CARD PAYMENT','','','EXAMPLE CARD PAYMENT','row:2025-01-15|-20000|EXAMPLE CARD PAYMENT','2025-01-15|-20000|EXAMPLE CARD PAYMENT',1,1,29,'manual',NULL,NULL,'Client lunch','2026-11-07 16:57:41');
INSERT INTO "transactions" VALUES(4,1,'2025-01-20',-1200,'MONTHLY SERVICE FEE','','','MONTHLY SERVICE FEE','row:2025-01-20|-1200|MONTHLY SERVICE FEE','2025-01-20|-1200|MONTHLY SERVICE FEE',1,1,33,'rule',3,NULL,'','2026-11-07 16:57:41');
INSERT INTO "transactions" VALUES(5,1,'2025-02-03',310050,'CLIENT HARBOR INVOICE 1002','','','CLIENT HARBOR INVOICE','row:2025-02-03|310050|CLIENT HARBOR INVOICE 1002','2025-02-03|310050|CLIENT HARBOR INVOICE 1002',1,1,4,'rule',1,NULL,'','2026-11-07 16:57:41');
INSERT INTO "transactions" VALUES(6,1,'2025-02-06',-85000,'REDWOOD PLAZA RENT','','','REDWOOD PLAZA RENT','row:2025-02-06|-85000|REDWOOD PLAZA RENT','2025-02-06|-85000|REDWOOD PLAZA RENT',1,1,24,'rule',2,NULL,'','2026-11-07 16:57:41');
INSERT INTO "transactions" VALUES(7,1,'2025-02-14',-7525,'COUNTY BUSINESS LICENSE','','','COUNTY BUSINESS LICENSE','row:2025-02-14|-7525|COUNTY BUSINESS LICENSE','2025-02-14|-7525|COUNTY BUSINESS LICENSE',1,1,27,'rule',4,NULL,'','2026-11-07 16:57:41');
INSERT INTO "transactions" VALUES(8,1,'2025-02-20',-1200,'MONTHLY SERVICE FEE','','','MONTHLY SERVICE FEE','row:2025-02-20|-1200|MONTHLY SERVICE FEE','2025-02-20|-1200|MONTHLY SERVICE FEE',1,1,33,'rule',3,NULL,'','2026-11-07 16:57:41');
INSERT INTO "transactions" VALUES(9,1,'2025-03-03',425000,'CLIENT NORTHWIND INVOICE 1003','','','CLIENT NORTHWIND INVOICE','row:2025-03-03|425000|CLIENT NORTHWIND INVOICE 1003','2025-03-03|425000|CLIENT NORTHWIND INVOICE 1003',1,2,4,'rule',1,NULL,'','2026-11-07 16:57:41');
INSERT INTO "transactions" VALUES(10,1,'2025-03-06',-85000,'REDWOOD PLAZA RENT','','','REDWOOD PLAZA RENT','row:2025-03-06|-85000|REDWOOD PLAZA RENT','2025-03-06|-85000|REDWOOD PLAZA RENT',1,2,24,'rule',2,NULL,'','2026-11-07 16:57:41');
INSERT INTO "transactions" VALUES(11,1,'2025-03-10',-150000,'OWNER DRAW TO PERSONAL','','','OWNER DRAW TO','row:2025-03-10|-150000|OWNER DRAW TO PERSONAL','2025-03-10|-150000|OWNER DRAW TO PERSONAL',1,2,NULL,NULL,NULL,NULL,'','2026-11-07 16:57:41');
INSERT INTO "transactions" VALUES(12,1,'2025-03-15',-31247,'EXAMPLE CARD PAYMENT','','','EXAMPLE CARD PAYMENT','row:2025-03-15|-31247|EXAMPLE CARD PAYMENT','2025-03-15|-31247|EXAMPLE CARD PAYMENT',1,2,NULL,NULL,NULL,NULL,'','2026-11-07 16:57:41');
INSERT INTO "transactions" VALUES(13,1,'2025-03-20',-1200,'MONTHLY SERVICE FEE','','','MONTHLY SERVICE FEE','row:2025-03-20|-1200|MONTHLY SERVICE FEE','2025-03-20|-1200|MONTHLY SERVICE FEE',1,2,33,'rule',3,NULL,'','2026-11-07 16:57:41');
INSERT INTO "transactions" VALUES(14,1,'2025-03-28',137,'INTEREST PAID','','','INTEREST PAID','row:2025-03-28|137|INTEREST PAID','2025-03-28|137|INTEREST PAID',1,2,6,'rule',10,NULL,'','2026-11-07 16:57:41');
INSERT INTO "transactions" VALUES(15,2,'2025-01-06',-1840,'SQ *BLUE FERN CAFE 0105 OGDEN UT','','','BLUE FERN CAFE','row:2025-01-06|-1840|SQ *BLUE FERN CAFE 0105 OGDEN UT','2025-01-06|-1840|SQ *BLUE FERN CAFE 0105 OGDEN UT',1,3,NULL,NULL,NULL,NULL,'','2026-11-07 16:57:42');
INSERT INTO "transactions" VALUES(16,2,'2025-01-09',-625,'SQ *BLUE FERN CAFE 0109 OGDEN UT','','','BLUE FERN CAFE','row:2025-01-09|-625|SQ *BLUE FERN CAFE 0109 OGDEN UT','2025-01-09|-625|SQ *BLUE FERN CAFE 0109 OGDEN UT',1,3,NULL,NULL,NULL,NULL,'','2026-11-07 16:57:42');
INSERT INTO "transactions" VALUES(17,2,'2025-01-09',-625,'SQ *BLUE FERN CAFE 0109 OGDEN UT','','','BLUE FERN CAFE','row:2025-01-09|-625|SQ *BLUE FERN CAFE 0109 OGDEN UT','2025-01-09|-625|SQ *BLUE FERN CAFE 0109 OGDEN UT',2,3,NULL,NULL,NULL,NULL,'','2026-11-07 16:57:42');
INSERT INTO "transactions" VALUES(18,2,'2025-01-12',-1600,'NOTION LABS SUBSCRIPTION','','','NOTION LABS SUBSCRIPTION','row:2025-01-12|-1600|NOTION LABS SUBSCRIPTION','2025-01-12|-1600|NOTION LABS SUBSCRIPTION',1,3,32,'rule',5,NULL,'','2026-11-07 16:57:42');
INSERT INTO "transactions" VALUES(19,2,'2025-01-15',20000,'PAYMENT THANK YOU','','','PAYMENT THANK YOU','row:2025-01-15|20000|PAYMENT THANK YOU','2025-01-15|20000|PAYMENT THANK YOU',1,3,NULL,NULL,NULL,NULL,'','2026-11-07 16:57:42');
INSERT INTO "transactions" VALUES(20,2,'2025-01-22',-41280,'SKYWAY AIR 0062','','','SKYWAY AIR','row:2025-01-22|-41280|SKYWAY AIR 0062','2025-01-22|-41280|SKYWAY AIR 0062',1,3,28,'rule',6,NULL,'','2026-11-07 16:57:42');
INSERT INTO "transactions" VALUES(21,2,'2025-02-12',-1600,'NOTION LABS SUBSCRIPTION','','','NOTION LABS SUBSCRIPTION','row:2025-02-12|-1600|NOTION LABS SUBSCRIPTION','2025-02-12|-1600|NOTION LABS SUBSCRIPTION',1,3,32,'rule',5,NULL,'','2026-11-07 16:57:42');
INSERT INTO "transactions" VALUES(22,2,'2025-02-18',11280,'SKYWAY AIR REFUND','','','SKYWAY AIR REFUND','row:2025-02-18|11280|SKYWAY AIR REFUND','2025-02-18|11280|SKYWAY AIR REFUND',1,3,28,'rule',6,NULL,'','2026-11-07 16:57:42');
INSERT INTO "transactions" VALUES(23,2,'2025-02-25',-6419,'STAPLE HUT 8812','','','STAPLE HUT','row:2025-02-25|-6419|STAPLE HUT 8812','2025-02-25|-6419|STAPLE HUT 8812',1,3,21,'rule',8,NULL,'','2026-11-07 16:57:42');
INSERT INTO "transactions" VALUES(24,2,'2025-03-12',-1600,'NOTION LABS SUBSCRIPTION','','','NOTION LABS SUBSCRIPTION','row:2025-03-12|-1600|NOTION LABS SUBSCRIPTION','2025-03-12|-1600|NOTION LABS SUBSCRIPTION',1,3,32,'rule',5,NULL,'','2026-11-07 16:57:42');
INSERT INTO "transactions" VALUES(25,2,'2025-03-15',31247,'PAYMENT THANK YOU','','','PAYMENT THANK YOU','row:2025-03-15|31247|PAYMENT THANK YOU','2025-03-15|31247|PAYMENT THANK YOU',1,3,NULL,NULL,NULL,NULL,'','2026-11-07 16:57:42');
INSERT INTO "transactions" VALUES(26,2,'2025-03-19',-23000,'HILLSIDE HOTEL','','','HILLSIDE HOTEL','row:2025-03-19|-23000|HILLSIDE HOTEL','2025-03-19|-23000|HILLSIDE HOTEL',1,3,28,'rule',7,NULL,'','2026-11-07 16:57:42');
INSERT INTO "transactions" VALUES(27,2,'2025-03-22',-15000,'NEON ADS','','','NEON ADS','row:2025-03-22|-15000|NEON ADS','2025-03-22|-15000|NEON ADS',1,3,11,'rule',9,NULL,'','2026-11-07 16:57:42');
INSERT INTO "transactions" VALUES(28,3,'2025-01-10',240000,'SHOPFRONT PAYOUT','','','SHOPFRONT PAYOUT','row:2025-01-10|240000|SHOPFRONT PAYOUT','2025-01-10|240000|SHOPFRONT PAYOUT',1,4,39,'rule',12,NULL,'','2026-11-07 16:57:43');
INSERT INTO "transactions" VALUES(29,3,'2025-01-12',-98000,'WHOLESALE WIDGETS','','','WHOLESALE WIDGETS','row:2025-01-12|-98000|WHOLESALE WIDGETS','2025-01-12|-98000|WHOLESALE WIDGETS',1,4,42,'rule',13,NULL,'','2026-11-07 16:57:43');
INSERT INTO "transactions" VALUES(30,3,'2025-01-25',-4500,'SHOPFRONT CUSTOMER REFUND','','','SHOPFRONT CUSTOMER REFUND','row:2025-01-25|-4500|SHOPFRONT CUSTOMER REFUND','2025-01-25|-4500|SHOPFRONT CUSTOMER REFUND',1,4,40,'rule',11,NULL,'','2026-11-07 16:57:43');
INSERT INTO "transactions" VALUES(31,3,'2025-02-10',187525,'SHOPFRONT PAYOUT','','','SHOPFRONT PAYOUT','row:2025-02-10|187525|SHOPFRONT PAYOUT','2025-02-10|187525|SHOPFRONT PAYOUT',1,4,39,'rule',12,NULL,'','2026-11-07 16:57:43');
INSERT INTO "transactions" VALUES(32,3,'2025-02-11',-21040,'PACKWELL BOXES','','','PACKWELL BOXES','row:2025-02-11|-21040|PACKWELL BOXES','2025-02-11|-21040|PACKWELL BOXES',1,4,44,'rule',14,NULL,'','2026-11-07 16:57:43');
INSERT INTO "transactions" VALUES(33,3,'2025-02-28',-8860,'SHIPEASY POSTAGE','','','SHIPEASY POSTAGE','row:2025-02-28|-8860|SHIPEASY POSTAGE','2025-02-28|-8860|SHIPEASY POSTAGE',1,4,45,'rule',15,NULL,'','2026-11-07 16:57:43');
INSERT INTO "transactions" VALUES(34,3,'2025-03-01',-2900,'HOSTLY WEBSITE PLAN','','','HOSTLY WEBSITE PLAN','row:2025-03-01|-2900|HOSTLY WEBSITE PLAN','2025-03-01|-2900|HOSTLY WEBSITE PLAN',1,4,67,'rule',16,NULL,'','2026-11-07 16:57:43');
INSERT INTO "transactions" VALUES(35,3,'2025-03-10',301000,'SHOPFRONT PAYOUT','','','SHOPFRONT PAYOUT','row:2025-03-10|301000|SHOPFRONT PAYOUT','2025-03-10|301000|SHOPFRONT PAYOUT',1,4,39,'rule',12,NULL,'','2026-11-07 16:57:43');
INSERT INTO "transactions" VALUES(36,3,'2025-03-15',-120000,'WHOLESALE WIDGETS','','','WHOLESALE WIDGETS','row:2025-03-15|-120000|WHOLESALE WIDGETS','2025-03-15|-120000|WHOLESALE WIDGETS',1,4,42,'rule',13,NULL,'','2026-11-07 16:57:43');
CREATE INDEX transactions_by_date ON transactions (account_id, posted_on);
CREATE INDEX transactions_by_category ON transactions (category_id, posted_on);
CREATE INDEX transactions_by_natural_key ON transactions (account_id, natural_key);
CREATE INDEX import_rows_by_transaction ON import_rows (transaction_id);
COMMIT;
