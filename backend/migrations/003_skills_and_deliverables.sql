-- Per-document skills + uploaded deliverables.
--
-- skill: how this document type is produced — which upstream documents to
-- pull from, what to take from each, granularity, tools/apps to use. Written
-- by the template owner (or Claude), read by Claude when generating the
-- document. Lives on the template node but stays editable after publication:
-- refining a skill is guidance, not a structural change.
--
-- attachments.kind: 'reference' (default — originals, supporting files) or
-- 'deliverable' (this file IS the document, e.g. an AutoCAD P&ID exported to
-- PDF that no structured section will ever replace).

ALTER TABLE document_type_nodes ADD COLUMN skill TEXT NOT NULL DEFAULT '';
ALTER TABLE attachments ADD COLUMN kind TEXT NOT NULL DEFAULT 'reference';
