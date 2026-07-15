-- Background sync poller support: remember which remote etag we already
-- reported a conflict for, so a standing conflict is logged once in the
-- document activity instead of every polling pass.

ALTER TABLE sharepoint_links ADD COLUMN conflict_etag TEXT NOT NULL DEFAULT '';
