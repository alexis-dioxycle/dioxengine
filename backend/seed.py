"""Example workflow template: electrolyzer basic engineering (idempotent, boot-time).

Same seed as the portal Phase-1 app: core docs bod → pfd → {hmb, sel, pid} →
{lines, cn}, plus 5 procurement package chains rfq_<pkg> → bid_<pkg> →
award_<pkg>. Packages, never tags: equipment items are rows inside the package
documents; comparison tables are long-format (one row per criterion × vendor);
vendors are data rows, not users.
"""
from datetime import datetime

from sqlalchemy.orm import Session

from models import (
    DocumentTypeNode, TemplateEdge, TemplateOwner, TemplateVersion,
    WorkflowTemplate,
)

SEED_TEMPLATE_NAME = "Electrolyzer Basic Engineering"


def _table(*cols):
    return [{"key": k, "label": l, "type": t} for k, l, t in cols]


def _nodes_and_edges():
    nodes = [
        dict(node_key="bod", name="Basis of Design", author_role="Process Engineer",
             reviewer_role="Engineering Manager",
             description="Design capacity, feedstocks, utilities, battery limits.",
             content_schema={"sections": [
                 {"key": "scope", "title": "Scope & capacity", "type": "text"},
                 {"key": "assumptions", "title": "Assumptions", "type": "text"}]}),
        dict(node_key="pfd", name="Process Flow Diagram", author_role="Process Engineer",
             reviewer_role="Lead Process Engineer",
             description="Main process streams and unit operations.",
             content_schema={"sections": [
                 {"key": "streams", "title": "Stream register", "type": "table",
                  "columns": _table(("stream", "Stream", "text"), ("from_", "From", "text"),
                                    ("to", "To", "text"), ("fluid", "Fluid", "text"),
                                    ("flow_kgh", "Flow (kg/h)", "number"))},
                 {"key": "notes", "title": "Notes", "type": "text"}]}),
        dict(node_key="hmb", name="Heat & Material Balance", author_role="Process Engineer",
             reviewer_role="Lead Process Engineer",
             description="Stream conditions solved from the PFD.",
             content_schema={"sections": [
                 {"key": "balance", "title": "Stream conditions", "type": "table",
                  "columns": _table(("stream", "Stream", "text"), ("t_c", "T (°C)", "number"),
                                    ("p_barg", "P (barg)", "number"),
                                    ("flow_kgh", "Flow (kg/h)", "number"))}]}),
        dict(node_key="sel", name="Sized Equipment List", author_role="Process Engineer",
             reviewer_role="Lead Process Engineer",
             description="All equipment with duty and design conditions.",
             content_schema={"sections": [
                 {"key": "equipment", "title": "Equipment", "type": "table",
                  "columns": _table(("tag", "Tag", "text"), ("service", "Service", "text"),
                                    ("type", "Type", "text"), ("duty", "Size / duty", "text"),
                                    ("design_p_barg", "Design P (barg)", "number"),
                                    ("material", "Material", "text"))}]}),
        dict(node_key="pid", name="P&ID", author_role="Process Engineer",
             reviewer_role="Lead Process Engineer",
             description="Piping & instrumentation; the line/tag register is the structured shadow of the drawing.",
             content_schema={"sections": [
                 {"key": "line_register", "title": "Line register", "type": "table",
                  "columns": _table(("line_no", "Line no.", "text"), ("from_", "From", "text"),
                                    ("to", "To", "text"), ("fluid", "Fluid", "text"))},
                 {"key": "notes", "title": "Drawing notes", "type": "text"}]}),
        dict(node_key="lines", name="Lines List", author_role="Piping Engineer",
             reviewer_role="Lead Process Engineer",
             description="All process lines with size, length and pressure drop.",
             content_schema={"sections": [
                 {"key": "lines", "title": "Lines", "type": "table",
                  "columns": _table(("line_no", "Line no.", "text"), ("fluid", "Fluid", "text"),
                                    ("size_dn", "Size (DN)", "number"),
                                    ("length_m", "Length (m)", "number"),
                                    ("dp_kpa", "ΔP (kPa)", "number"))}]}),
        dict(node_key="cn", name="Control Narrative", author_role="Process Engineer",
             reviewer_role="Automation Engineer", receiver_roles=["Programmer"],
             description="How the unit is controlled, started and shut down, in words.",
             content_schema={"sections": [
                 {"key": "philosophy", "title": "Control philosophy", "type": "text"},
                 {"key": "startup", "title": "Start-up sequence", "type": "text"},
                 {"key": "shutdown", "title": "Shutdown & interlocks", "type": "text"}]}),
    ]
    edges = [("bod", "pfd"), ("pfd", "hmb"), ("pfd", "sel"), ("hmb", "sel"),
             ("pfd", "pid"), ("sel", "pid"), ("pid", "lines"), ("pid", "cn")]

    packages = [
        ("pumps", "Pumps", "Rotating Equipment Engineer",
         _table(("tag", "Tag", "text"), ("service", "Service", "text"),
                ("type", "Type", "text"), ("flow_m3h", "Flow (m³/h)", "number"),
                ("head_m", "Head (m)", "number"), ("npsh_a_m", "NPSHa (m)", "number"),
                ("material", "Material", "text")),
         ["sel", "lines"]),
        ("vessels", "Vessels", "Mechanical Engineer",
         _table(("tag", "Tag", "text"), ("service", "Service", "text"),
                ("volume_m3", "Volume (m³)", "number"),
                ("design_p_barg", "Design P (barg)", "number"),
                ("design_t_c", "Design T (°C)", "number"),
                ("material", "Material", "text"), ("code", "Design code", "text")),
         ["sel"]),
        ("hx", "Heat Exchangers", "Mechanical Engineer",
         _table(("tag", "Tag", "text"), ("service", "Service", "text"),
                ("type", "Type", "text"), ("duty_kw", "Duty (kW)", "number"),
                ("area_m2", "Area (m²)", "number"),
                ("design_p_barg", "Design P (barg)", "number"),
                ("material", "Material", "text")),
         ["sel", "hmb"]),
        ("valves", "Valves", "Piping Engineer",
         _table(("tag", "Tag", "text"), ("type", "Type", "text"),
                ("size_dn", "Size (DN)", "number"), ("rating", "Rating class", "text"),
                ("material", "Material", "text"), ("qty", "Qty", "number")),
         ["pid", "lines"]),
        ("instr", "Instrumentation", "Automation Engineer",
         _table(("tag", "Tag", "text"), ("service", "Service", "text"),
                ("type", "Type", "text"), ("range", "Range", "text"),
                ("signal", "Signal", "text"), ("area_cert", "Ex cert", "text")),
         ["pid", "cn"]),
    ]
    for key, label, role, scope_cols, upstream in packages:
        nodes += [
            dict(node_key=f"rfq_{key}", name=f"RFQ — {label}", author_role=role,
                 reviewer_role="Lead Process Engineer",
                 description=f"Request-for-quotation package for {label.lower()}: scope of supply (one row per item), requirements, vendors consulted.",
                 content_schema={"sections": [
                     {"key": "scope", "title": "Scope of supply", "type": "table",
                      "columns": scope_cols},
                     {"key": "requirements", "title": "Technical requirements", "type": "text"},
                     {"key": "vendors", "title": "Vendors consulted", "type": "table",
                      "columns": _table(("vendor", "Vendor", "text"),
                                        ("contact", "Contact", "text"),
                                        ("status", "Status", "text"))}]}),
            dict(node_key=f"bid_{key}", name=f"Bid Evaluation — {label}", author_role=role,
                 reviewer_role="Lead Process Engineer",
                 description="Offers received plus technical and commercial comparison (long format: one row per criterion per vendor).",
                 content_schema={"sections": [
                     {"key": "offers", "title": "Offers register", "type": "table",
                      "columns": _table(("vendor", "Vendor", "text"), ("offer_ref", "Offer ref", "text"),
                                        ("rev", "Rev", "text"), ("price", "Price (€)", "number"),
                                        ("delivery_wks", "Delivery (wks)", "number"),
                                        ("validity", "Valid until", "text"))},
                     {"key": "technical", "title": "Technical comparison", "type": "table",
                      "columns": _table(("criterion", "Criterion", "text"), ("requirement", "Requirement", "text"),
                                        ("vendor", "Vendor", "text"), ("offered", "Offered", "text"),
                                        ("compliant", "Compliant", "text"), ("deviation", "Deviation", "text"))},
                     {"key": "commercial", "title": "Commercial comparison", "type": "table",
                      "columns": _table(("item", "Item", "text"), ("vendor", "Vendor", "text"),
                                        ("value", "Value", "text"), ("note", "Note", "text"))}]}),
            dict(node_key=f"award_{key}", name=f"Award — {label}", author_role=role,
                 reviewer_role="Procurement Manager",
                 description="Award recommendation; the selected vendor's data feeds downstream documents.",
                 content_schema={"sections": [
                     {"key": "recommendation", "title": "Recommendation & justification", "type": "text"},
                     {"key": "selected", "title": "Selected offer", "type": "table",
                      "columns": _table(("vendor", "Vendor", "text"), ("offer_ref", "Offer ref", "text"),
                                        ("price", "Price (€)", "number"),
                                        ("delivery_wks", "Delivery (wks)", "number"))}]}),
        ]
        edges += [(u, f"rfq_{key}") for u in upstream]
        edges += [(f"rfq_{key}", f"bid_{key}"), (f"bid_{key}", f"award_{key}")]
    return nodes, edges


def write_graph(db: Session, tv: TemplateVersion, nodes: list[dict], edges: list[tuple]):
    key_to_id = {}
    for n in nodes:
        node = DocumentTypeNode(template_version_id=tv.id, **n)
        db.add(node)
        db.flush()
        key_to_id[n["node_key"]] = node.id
    for f, t in edges:
        db.add(TemplateEdge(template_version_id=tv.id,
                            from_node_id=key_to_id[f], to_node_id=key_to_id[t]))


def seed_example(db: Session, owner_email: str = ""):
    if db.query(WorkflowTemplate).filter(WorkflowTemplate.name == SEED_TEMPLATE_NAME).first():
        return None
    t = WorkflowTemplate(
        name=SEED_TEMPLATE_NAME,
        description="Example workflow: design basis through control narrative for an electrolyzer unit, plus per-package procurement chains.",
        created_by=owner_email)
    db.add(t)
    db.flush()
    if owner_email:
        db.add(TemplateOwner(template_id=t.id, user_email=owner_email))
    tv = TemplateVersion(template_id=t.id, version_number=1, status="published",
                         created_by=owner_email, published_at=datetime.utcnow())
    db.add(tv)
    db.flush()
    nodes, edges = _nodes_and_edges()
    write_graph(db, tv, nodes, edges)
    db.commit()
    return t
