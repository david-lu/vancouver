CREATE TABLE `documents` (
	`id` text PRIMARY KEY NOT NULL,
	`title` text NOT NULL,
	`content` text NOT NULL,
	`url` text NOT NULL,
	`source` text,
	`embedding` text,
	`updated` text
);
--> statement-breakpoint
CREATE TABLE `layers` (
	`id` text PRIMARY KEY NOT NULL,
	`data` text NOT NULL
);
--> statement-breakpoint
CREATE TABLE `limits` (
	`key` text PRIMARY KEY NOT NULL,
	`count` integer NOT NULL
);
--> statement-breakpoint
CREATE TABLE `listings` (
	`id` text PRIMARY KEY NOT NULL,
	`address` text,
	`price` real,
	`lat` real,
	`lon` real,
	`beds` integer,
	`baths` real,
	`area` real,
	`type` text,
	`status` text,
	`url` text,
	`photos` text,
	`brokerage` text,
	`updated` text,
	`raw` text
);
--> statement-breakpoint
CREATE INDEX `listing_location` ON `listings` (`lat`,`lon`);--> statement-breakpoint
CREATE TABLE `permits` (
	`id` text PRIMARY KEY NOT NULL,
	`address` text,
	`description` text,
	`issued` text,
	`value` real,
	`type` text,
	`lat` real,
	`lon` real,
	`raw` text
);
--> statement-breakpoint
CREATE INDEX `permit_address` ON `permits` (`address`);--> statement-breakpoint
CREATE INDEX `permit_location` ON `permits` (`lat`,`lon`);--> statement-breakpoint
CREATE TABLE `properties` (
	`id` text PRIMARY KEY NOT NULL,
	`pid` text,
	`address` text NOT NULL,
	`street` text,
	`neighbourhood` text,
	`lat` real,
	`lon` real,
	`assessed` real,
	`land` real,
	`improvement` real,
	`tax` real,
	`year` integer,
	`built` integer,
	`zone` text,
	`type` text,
	`change` real,
	`history` text DEFAULT '[]' NOT NULL,
	`features` text DEFAULT '{}' NOT NULL,
	`parcel` text
);
--> statement-breakpoint
CREATE INDEX `property_location` ON `properties` (`lat`,`lon`);--> statement-breakpoint
CREATE INDEX `property_area_price` ON `properties` (`neighbourhood`,`assessed`);--> statement-breakpoint
CREATE INDEX `property_address` ON `properties` (`address`);--> statement-breakpoint
CREATE INDEX `property_pid` ON `properties` (`pid`);--> statement-breakpoint
CREATE TABLE `saved` (
	`user` text NOT NULL,
	`property` text NOT NULL,
	`note` text DEFAULT '',
	`created` text NOT NULL,
	PRIMARY KEY(`user`, `property`)
);
--> statement-breakpoint
CREATE TABLE `searches` (
	`id` text PRIMARY KEY NOT NULL,
	`user` text NOT NULL,
	`name` text NOT NULL,
	`filters` text NOT NULL,
	`created` text NOT NULL
);
--> statement-breakpoint
CREATE INDEX `search_user` ON `searches` (`user`);--> statement-breakpoint
CREATE TABLE `sources` (
	`id` text PRIMARY KEY NOT NULL,
	`title` text NOT NULL,
	`url` text NOT NULL,
	`downloaded` text,
	`records` integer,
	`bytes` integer,
	`sha256` text,
	`status` text,
	`detail` text
);

--> statement-breakpoint
CREATE VIRTUAL TABLE documents_fts USING fts5(id UNINDEXED,title,content);
--> statement-breakpoint
CREATE TRIGGER documents_ai AFTER INSERT ON documents BEGIN INSERT INTO documents_fts(id,title,content) VALUES(new.id,new.title,new.content); END;
--> statement-breakpoint
CREATE TRIGGER documents_au AFTER UPDATE ON documents BEGIN DELETE FROM documents_fts WHERE id=old.id; INSERT INTO documents_fts(id,title,content) VALUES(new.id,new.title,new.content); END;
--> statement-breakpoint
CREATE TRIGGER documents_ad AFTER DELETE ON documents BEGIN DELETE FROM documents_fts WHERE id=old.id; END;
--> statement-breakpoint
CREATE VIRTUAL TABLE permits_fts USING fts5(id UNINDEXED,address,description);
--> statement-breakpoint
CREATE TRIGGER permits_ai AFTER INSERT ON permits BEGIN INSERT INTO permits_fts(id,address,description) VALUES(new.id,new.address,new.description); END;
--> statement-breakpoint
CREATE TRIGGER permits_au AFTER UPDATE ON permits BEGIN DELETE FROM permits_fts WHERE id=old.id; INSERT INTO permits_fts(id,address,description) VALUES(new.id,new.address,new.description); END;
--> statement-breakpoint
CREATE TRIGGER permits_ad AFTER DELETE ON permits BEGIN DELETE FROM permits_fts WHERE id=old.id; END;
