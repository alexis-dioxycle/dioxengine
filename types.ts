export interface Me { id: number; email: string; name: string; role: string }

export interface Column { key: string; label: string; type: 'text' | 'number' }
export interface Section { key: string; title: string; type: 'text' | 'table'; columns?: Column[] }
export interface ContentSchema { sections: Section[] }
export type Row = Record<string, string | number>;
export type Content = Record<string, string | Row[]>;

export interface VersionInfo {
  version_number: number; status: string; created_by: string;
  created_at: string | null; updated_at?: string | null;
  submitted_at: string | null; reviewed_by: string | null;
  reviewed_at: string | null; review_comment: string;
  based_on: Record<string, number>;
  content?: Content;
}

export interface CommentT {
  id: number; section_key: string; row_index: number | null;
  parent_id: number | null; author_email: string; author_kind: 'user' | 'assistant';
  body: string; status: 'open' | 'resolved'; resolved_by: string | null;
  resolved_at: string | null; created_at: string | null;
}

export interface DocSummary {
  id: number; node_id: number; node_key: string; name: string; description: string;
  author_email: string; reviewer_email: string; receiver_emails: string[];
  author_role: string; reviewer_role: string;
  latest: VersionInfo | null; approved: VersionInfo | null;
  stale: boolean; stale_reasons: string[]; open_comments: number;
}

export interface DocFull extends DocSummary {
  project_id: number; project_name: string;
  content_schema: ContentSchema;
  can_edit: boolean; can_review: boolean;
  latest_content: Content; latest_status: string | null;
  latest_version_number: number | null; latest_updated_at: string | null;
  versions: VersionInfo[];
  upstream: { document_id: number; name: string; approved_version: number | null }[];
  comments: CommentT[];
}

export interface ProjectSummary {
  id: number; name: string; description: string; created_by: string;
  created_at: string | null; template_name: string; template_version: number;
  members: string[]; n_documents: number; n_approved: number;
}

export interface ProjectFull {
  id: number; name: string; description: string;
  template_name: string; template_version: number; template_version_id: number;
  created_by: string; members: string[]; can_manage_members: boolean;
  edges: { from_node_id: number; to_node_id: number }[];
  documents: DocSummary[];
}

export interface TemplateT {
  id: number; name: string; description: string; created_by: string;
  owners: string[]; users: string[]; is_owner: boolean;
  versions: { id: number; version_number: number; status: string }[];
}

export interface Activity {
  actor_email: string; actor_kind: 'user' | 'assistant'; action: string;
  payload: Record<string, unknown>; created_at: string | null;
}
