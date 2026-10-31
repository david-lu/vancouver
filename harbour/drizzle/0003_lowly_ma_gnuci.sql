CREATE TABLE `addresses` (
	`id` text PRIMARY KEY NOT NULL,
	`address` text NOT NULL,
	`municipality` text NOT NULL,
	`lat` real,
	`lon` real,
	`source` text
);
--> statement-breakpoint
CREATE INDEX `civic_address` ON `addresses` (`address`);--> statement-breakpoint
CREATE INDEX `civic_municipality` ON `addresses` (`municipality`);
--> statement-breakpoint
CREATE VIRTUAL TABLE addresses_fts USING fts5(id UNINDEXED,address,municipality,content='addresses',content_rowid='rowid');
--> statement-breakpoint
CREATE TRIGGER addresses_ai AFTER INSERT ON addresses BEGIN INSERT INTO addresses_fts(rowid,id,address,municipality) VALUES(new.rowid,new.id,new.address,new.municipality); END;
--> statement-breakpoint
CREATE TRIGGER addresses_au AFTER UPDATE ON addresses BEGIN INSERT INTO addresses_fts(addresses_fts,rowid,id,address,municipality) VALUES('delete',old.rowid,old.id,old.address,old.municipality); INSERT INTO addresses_fts(rowid,id,address,municipality) VALUES(new.rowid,new.id,new.address,new.municipality); END;
--> statement-breakpoint
CREATE TRIGGER addresses_ad AFTER DELETE ON addresses BEGIN INSERT INTO addresses_fts(addresses_fts,rowid,id,address,municipality) VALUES('delete',old.rowid,old.id,old.address,old.municipality); END;
