"""SQLAlchemy models.

Domain (ported from the portal Phase-1 app): workflow templates are versioned
DAGs of document types; a project instantiates a published template version;
each node becomes a document with versioned, reviewable structured content.
Staleness = a document whose latest approved version was based on upstream
revisions that have since been superseded.

New here vs Phase 1: comments (anchored to a section, optionally a table row,
resolvable — including by Claude via MCP) and an activity log that records who
changed what, human or assistant.
"""
from datetime import datetime

from sqlalchemy import (
    JSON, Column, DateTime, ForeignKey, Integer, LargeBinary, String, Text,
    UniqueConstraint,
)
from sqlalchemy.orm import deferred
from sqlalchemy.orm import relationship

from database import Base


class User(Base):
    """Directory of everyone who has connected through the portal. The portal
    is the identity source (see dioxycle_auth); this table only backs the
    owner/member pickers, upserted on every request."""
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    email = Column(String, unique=True, index=True, nullable=False)
    name = Column(String, default="")
    role = Column(String, default="")
    first_seen = Column(DateTime, default=datetime.utcnow)
    last_seen = Column(DateTime, default=datetime.utcnow)


# ---------------------------------------------------------------- templates

class WorkflowTemplate(Base):
    __tablename__ = "workflow_templates"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    description = Column(Text, default="")
    created_by = Column(String, default="")  # email
    created_at = Column(DateTime, default=datetime.utcnow)

    versions = relationship("TemplateVersion", back_populates="template",
                            cascade="all, delete-orphan")


class TemplateVersion(Base):
    __tablename__ = "template_versions"
    id = Column(Integer, primary_key=True)
    template_id = Column(Integer, ForeignKey("workflow_templates.id"), nullable=False)
    version_number = Column(Integer, nullable=False)
    status = Column(String, default="draft")  # draft | published
    created_by = Column(String, default="")
    created_at = Column(DateTime, default=datetime.utcnow)
    published_at = Column(DateTime, nullable=True)

    template = relationship("WorkflowTemplate", back_populates="versions")
    nodes = relationship("DocumentTypeNode", back_populates="template_version",
                         cascade="all, delete-orphan",
                         order_by="DocumentTypeNode.id")
    edges = relationship("TemplateEdge", cascade="all, delete-orphan")


class DocumentTypeNode(Base):
    __tablename__ = "document_type_nodes"
    id = Column(Integer, primary_key=True)
    template_version_id = Column(Integer, ForeignKey("template_versions.id"), nullable=False)
    node_key = Column(String, nullable=False)
    name = Column(String, nullable=False)
    description = Column(Text, default="")
    # {"sections": [{key, title, type: "text"|"table", columns?: [{key,label,type}]}]}
    content_schema = Column(JSON, default=dict)
    author_role = Column(String, default="")
    reviewer_role = Column(String, default="")
    receiver_roles = Column(JSON, default=list)

    template_version = relationship("TemplateVersion", back_populates="nodes")
    __table_args__ = (UniqueConstraint("template_version_id", "node_key"),)


class TemplateEdge(Base):
    __tablename__ = "template_edges"
    id = Column(Integer, primary_key=True)
    template_version_id = Column(Integer, ForeignKey("template_versions.id"), nullable=False)
    from_node_id = Column(Integer, ForeignKey("document_type_nodes.id"), nullable=False)
    to_node_id = Column(Integer, ForeignKey("document_type_nodes.id"), nullable=False)


class TemplateOwner(Base):
    __tablename__ = "template_owners"
    template_id = Column(Integer, ForeignKey("workflow_templates.id"), primary_key=True)
    user_email = Column(String, primary_key=True)


class TemplateUser(Base):
    __tablename__ = "template_users"
    template_id = Column(Integer, ForeignKey("workflow_templates.id"), primary_key=True)
    user_email = Column(String, primary_key=True)


# ----------------------------------------------------------------- projects

class Project(Base):
    __tablename__ = "projects"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    description = Column(Text, default="")
    template_version_id = Column(Integer, ForeignKey("template_versions.id"), nullable=False)
    created_by = Column(String, default="")
    created_at = Column(DateTime, default=datetime.utcnow)

    template_version = relationship("TemplateVersion")
    documents = relationship("Document", back_populates="project",
                             cascade="all, delete-orphan",
                             order_by="Document.id")


class ProjectMember(Base):
    __tablename__ = "project_members"
    project_id = Column(Integer, ForeignKey("projects.id"), primary_key=True)
    user_email = Column(String, primary_key=True)


class Document(Base):
    __tablename__ = "documents"
    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)
    node_id = Column(Integer, ForeignKey("document_type_nodes.id"), nullable=False)
    author_email = Column(String, default="")
    reviewer_email = Column(String, default="")
    receiver_emails = Column(JSON, default=list)

    project = relationship("Project", back_populates="documents")
    node = relationship("DocumentTypeNode")
    versions = relationship("DocumentVersion", back_populates="document",
                            cascade="all, delete-orphan",
                            order_by="DocumentVersion.version_number")
    comments = relationship("Comment", back_populates="document",
                            cascade="all, delete-orphan",
                            order_by="Comment.id")


class DocumentVersion(Base):
    __tablename__ = "document_versions"
    id = Column(Integer, primary_key=True)
    document_id = Column(Integer, ForeignKey("documents.id"), nullable=False)
    version_number = Column(Integer, nullable=False)
    # draft -> submitted -> approved | rejected; approving supersedes prior approved
    status = Column(String, default="draft")
    content = Column(JSON, default=dict)  # {section_key: string | [rows]}
    created_by = Column(String, default="")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    submitted_at = Column(DateTime, nullable=True)
    reviewed_by = Column(String, nullable=True)
    reviewed_at = Column(DateTime, nullable=True)
    review_comment = Column(Text, default="")
    based_on = Column(JSON, default=dict)  # {upstream document_id: approved rev}

    document = relationship("Document", back_populates="versions")
    __table_args__ = (UniqueConstraint("document_id", "version_number"),)


class Comment(Base):
    __tablename__ = "comments"
    id = Column(Integer, primary_key=True)
    document_id = Column(Integer, ForeignKey("documents.id"), nullable=False)
    section_key = Column(String, nullable=False)
    row_index = Column(Integer, nullable=True)  # table sections only
    parent_id = Column(Integer, ForeignKey("comments.id"), nullable=True)
    author_email = Column(String, nullable=False)
    author_kind = Column(String, default="user")  # user | assistant
    body = Column(Text, nullable=False)
    status = Column(String, default="open")  # open | resolved (top-level only)
    resolved_by = Column(String, nullable=True)
    resolved_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    document = relationship("Document", back_populates="comments")
    replies = relationship("Comment", order_by="Comment.created_at")


class Attachment(Base):
    """The real files behind a document (PFD drawing, offer PDFs, issued
    datasheets). Structured sections stay the source of truth for the DAG;
    attachments are the human-readable originals. `data` is deferred so
    listings never load the bytes."""
    __tablename__ = "attachments"
    id = Column(Integer, primary_key=True)
    document_id = Column(Integer, ForeignKey("documents.id"), nullable=False)
    filename = Column(String, nullable=False)
    content_type = Column(String, default="application/octet-stream")
    size_bytes = Column(Integer, default=0)
    data = deferred(Column(LargeBinary, nullable=False))
    uploaded_by = Column(String, default="")
    created_at = Column(DateTime, default=datetime.utcnow)


class ActivityLog(Base):
    __tablename__ = "activity_log"
    id = Column(Integer, primary_key=True)
    document_id = Column(Integer, ForeignKey("documents.id"), nullable=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=True)
    actor_email = Column(String, default="")
    actor_kind = Column(String, default="user")  # user | assistant
    action = Column(String, nullable=False)  # draft_edit, submit, review, comment, ...
    payload = Column(JSON, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow)
