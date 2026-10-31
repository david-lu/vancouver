import { sqliteTable, text, real, integer, index, primaryKey } from 'drizzle-orm/sqlite-core';
export const properties = sqliteTable('properties', {
 id:text('id').primaryKey(), pid:text('pid'), address:text('address').notNull(), street:text('street'), neighbourhood:text('neighbourhood'), municipality:text('municipality').notNull().default('Vancouver'),
 lat:real('lat'), lon:real('lon'), assessed:real('assessed'), land:real('land'), improvement:real('improvement'), tax:real('tax'),
 year:integer('year'), built:integer('built'), zone:text('zone'), type:text('type'), change:real('change'),
 history:text('history').notNull().default('[]'), features:text('features').notNull().default('{}'), parcel:text('parcel'),
},t=>[index('property_location').on(t.lat,t.lon),index('property_map').on(t.lat,t.lon,t.assessed,t.id,t.municipality,t.type,t.neighbourhood),index('property_area_price').on(t.neighbourhood,t.assessed),index('property_municipality').on(t.municipality,t.assessed),index('property_address').on(t.address),index('property_pid').on(t.pid)]);
export const archive = sqliteTable('archive',{id:text('id').primaryKey(),pid:text('pid'),address:text('address'),years:text('years'),shard:text('shard')},t=>[index('archive_pid').on(t.pid),index('archive_address').on(t.address)]);
export const addresses = sqliteTable('addresses',{id:text('id').primaryKey(),address:text('address').notNull(),municipality:text('municipality').notNull(),lat:real('lat'),lon:real('lon'),source:text('source')},t=>[index('civic_address').on(t.address),index('civic_municipality').on(t.municipality)]);
export const permits = sqliteTable('permits', {
 id:text('id').primaryKey(),address:text('address'),description:text('description'),issued:text('issued'),value:real('value'),type:text('type'),lat:real('lat'),lon:real('lon'),raw:text('raw'),
},t=>[index('permit_address').on(t.address),index('permit_location').on(t.lat,t.lon)]);
export const layers = sqliteTable('layers',{id:text('id').primaryKey(),data:text('data').notNull()});
export const sources = sqliteTable('sources',{id:text('id').primaryKey(),title:text('title').notNull(),url:text('url').notNull(),downloaded:text('downloaded'),records:integer('records'),bytes:integer('bytes'),sha256:text('sha256'),status:text('status'),detail:text('detail')});
export const documents = sqliteTable('documents',{id:text('id').primaryKey(),title:text('title').notNull(),content:text('content').notNull(),url:text('url').notNull(),source:text('source'),embedding:text('embedding'),updated:text('updated')});
export const saved = sqliteTable('saved',{user:text('user').notNull(),property:text('property').notNull(),note:text('note').default(''),created:text('created').notNull()},t=>[primaryKey({columns:[t.user,t.property]})]);
export const searches = sqliteTable('searches',{id:text('id').primaryKey(),user:text('user').notNull(),name:text('name').notNull(),filters:text('filters').notNull(),created:text('created').notNull()},t=>[index('search_user').on(t.user)]);
export const listings = sqliteTable('listings',{id:text('id').primaryKey(),address:text('address'),price:real('price'),lat:real('lat'),lon:real('lon'),beds:integer('beds'),baths:real('baths'),area:real('area'),type:text('type'),status:text('status'),url:text('url'),photos:text('photos'),brokerage:text('brokerage'),updated:text('updated'),raw:text('raw')},t=>[index('listing_location').on(t.lat,t.lon)]);
export const limits = sqliteTable('limits',{key:text('key').primaryKey(),count:integer('count').notNull()});
