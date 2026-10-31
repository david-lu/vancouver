CREATE TABLE `archive` (
	`id` text PRIMARY KEY NOT NULL,
	`pid` text,
	`address` text,
	`years` text,
	`shard` text
);
--> statement-breakpoint
CREATE INDEX `archive_pid` ON `archive` (`pid`);--> statement-breakpoint
CREATE INDEX `archive_address` ON `archive` (`address`);--> statement-breakpoint
CREATE INDEX `property_municipality` ON `properties` (`municipality`,`assessed`);