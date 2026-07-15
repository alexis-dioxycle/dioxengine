-- Deterministic tools attached to a document type: small Dioxycle Apps
-- endpoints (pressure drop, line sizing, equipment calcs...) the assistant
-- may call while producing the document. Each entry:
--   {"name": "pressure_drop", "description": "...", "url": "https://...",
--    "method": "GET"|"POST", "params": "human description of the inputs"}
-- Callable via the use_document_tool MCP tool; hosts restricted by the
-- TOOL_ALLOWED_HOSTS env allowlist (default apps.dioxycle.com). Like skills,
-- tools stay editable on published versions (guidance, not structure).

ALTER TABLE document_type_nodes ADD COLUMN tools JSONB NOT NULL DEFAULT '[]';
