"""Blueprint do painel admin: /api/v1/admin."""

from datetime import datetime, timedelta
from io import StringIO
import csv

from flask import Blueprint, current_app, jsonify, request, make_response
from sqlalchemy import func, or_, desc

from ..extensions import db
from ..models import Lead, LeadType, LeadStatus
from .decorators import admin_required
from services.leads import update_lead_status, convert_lead, LeadError, LeadAlreadyConvertedError


admin_bp = Blueprint("admin", __name__, url_prefix="/api/v1/admin")


def _parse_bool(val: str | None) -> bool | None:
    if val is None:
        return None
    return val.lower() in ("true", "1", "yes")


def _parse_int(val: str | None, default: int) -> int:
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


def _erro(code: str, message: str, status: int):
    """Resposta de erro no formato padrão da API."""
    return jsonify({"error": {"code": code, "message": message}}), status


# ---------------------------------------------------------------------------
# GET /api/v1/admin/leads — listagem com filtros, paginação e ordenação
# ---------------------------------------------------------------------------
@admin_bp.get("/leads")
@admin_required
def list_leads():
    # Parâmetros de query
    page = _parse_int(request.args.get("page"), 1)
    per_page = _parse_int(request.args.get("per_page"), 20)
    status = request.args.get("status")
    lead_type = request.args.get("type")
    bairro = request.args.get("bairro")
    search = request.args.get("search")
    sort = request.args.get("sort", "-created_at")  # -created_at = mais recente primeiro

    query = Lead.query

    # Filtros
    if status:
        try:
            query = query.filter(Lead.status == LeadStatus(status))
        except ValueError:
            return _erro("invalid_status", f"Status inválido: {status}", 400)

    if lead_type:
        try:
            query = query.filter(Lead.type == LeadType(lead_type))
        except ValueError:
            return _erro("invalid_type", f"Tipo inválido: {lead_type}", 400)

    if bairro:
        query = query.filter(Lead.bairro.ilike(f"%{bairro}%"))

    if search:
        search_term = f"%{search}%"
        query = query.filter(
            or_(
                Lead.name.ilike(search_term),
                Lead.email.ilike(search_term),
                Lead.phone.ilike(search_term),
            )
        )

    # Ordenação
    if sort.startswith("-"):
        order_col = getattr(Lead, sort[1:], Lead.created_at)
        query = query.order_by(desc(order_col))
    else:
        order_col = getattr(Lead, sort, Lead.created_at)
        query = query.order_by(order_col)

    # Paginação
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)

    return jsonify({
        "leads": [lead.to_dict() for lead in pagination.items],
        "pagination": {
            "page": pagination.page,
            "per_page": pagination.per_page,
            "total": pagination.total,
            "pages": pagination.pages,
            "has_next": pagination.has_next,
            "has_prev": pagination.has_prev,
        }
    }), 200


# ---------------------------------------------------------------------------
# GET /api/v1/admin/leads/<id> — detalhe do lead
# ---------------------------------------------------------------------------
@admin_bp.get("/leads/<int:lead_id>")
@admin_required
def get_lead(lead_id: int):
    lead = db.session.get(Lead, lead_id)
    if not lead:
        return _erro("not_found", "Lead não encontrado.", 404)

    return jsonify({"lead": lead.to_dict()}), 200


# ---------------------------------------------------------------------------
# PATCH /api/v1/admin/leads/<id>/status — alterar status
# ---------------------------------------------------------------------------
@admin_bp.patch("/leads/<int:lead_id>/status")
@admin_required
def change_lead_status(lead_id: int):
    data = request.get_json(silent=True) or {}
    status_str = data.get("status")

    if not status_str:
        return _erro("missing_status", "Informe o status desejado.", 400)

    try:
        new_status = LeadStatus(status_str)
    except ValueError:
        return _erro("invalid_status", f"Status inválido: {status_str}", 400)

    # Validação manual de transição (update_lead_status já faz isso, mas damos feedback melhor)
    lead = db.session.get(Lead, lead_id)
    if not lead:
        return _erro("not_found", "Lead não encontrado.", 404)

    valid_transitions = {
        LeadStatus.new: [LeadStatus.contacted, LeadStatus.qualified, LeadStatus.lost, LeadStatus.converted],
        LeadStatus.contacted: [LeadStatus.qualified, LeadStatus.lost, LeadStatus.converted],
        LeadStatus.qualified: [LeadStatus.converted, LeadStatus.lost],
        LeadStatus.converted: [],
        LeadStatus.lost: [LeadStatus.new],
    }

    if new_status not in valid_transitions.get(lead.status, []):
        return _erro(
            "invalid_transition",
            f"Transição inválida de {lead.status.value} para {new_status.value}. "
            f"Permitidas: {[s.value for s in valid_transitions.get(lead.status, [])]}",
            400
        )

    try:
        updated = update_lead_status(lead_id, new_status)
        db.session.commit()
    except LeadError as e:
        db.session.rollback()
        return _erro("status_error", str(e), 400)

    return jsonify({"lead": updated.to_dict()}), 200


# ---------------------------------------------------------------------------
# POST /api/v1/admin/leads/<id>/convert — converter lead
# ---------------------------------------------------------------------------
@admin_bp.post("/leads/<int:lead_id>/convert")
@admin_required
def convert_lead_endpoint(lead_id: int):
    data = request.get_json(silent=True) or {}
    password = data.get("password")
    establishment_data = data.get("establishment")

    try:
        result = convert_lead(lead_id, password=password, establishment_data=establishment_data)
        db.session.commit()
    except LeadAlreadyConvertedError as e:
        db.session.rollback()
        return jsonify({
            "message": "Lead já convertido anteriormente.",
            "lead": e.args[0] if e.args else {},
            "user_id": e.existing_user_id,
            "establishment_id": e.existing_establishment_id,
        }), 200
    except LeadError as e:
        db.session.rollback()
        return _erro("conversion_error", str(e), 400)
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Erro ao converter lead")
        return _erro("internal_error", "Erro interno no servidor.", 500)

    response = {"message": "Lead convertido com sucesso!", "lead": result["lead"].to_dict()}
    if "user" in result:
        response["user"] = result["user"].to_dict()
    if "establishment" in result:
        response["establishment"] = result["establishment"].to_dict()

    return jsonify(response), 201


# ---------------------------------------------------------------------------
# GET /api/v1/admin/stats — estatísticas
# ---------------------------------------------------------------------------
@admin_bp.get("/stats")
@admin_required
def stats():
    now = datetime.utcnow()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_ago = today_start - timedelta(days=7)

    # Total por status
    status_counts = db.session.query(Lead.status, func.count(Lead.id)).group_by(Lead.status).all()
    by_status = {s.value: c for s, c in status_counts}

    # Total por tipo
    type_counts = db.session.query(Lead.type, func.count(Lead.id)).group_by(Lead.type).all()
    by_type = {t.value: c for t, c in type_counts}

    # Hoje
    today_count = db.session.query(func.count(Lead.id)).filter(Lead.created_at >= today_start).scalar()

    # Últimos 7 dias
    week_count = db.session.query(func.count(Lead.id)).filter(Lead.created_at >= week_ago).scalar()

    # Por dia (últimos 7 dias) - para gráfico
    daily = db.session.query(
        func.date(Lead.created_at).label("day"),
        func.count(Lead.id).label("count")
    ).filter(Lead.created_at >= week_ago).group_by(func.date(Lead.created_at)).all()
    daily_data = {str(d): c for d, c in daily}

    return jsonify({
        "by_status": by_status,
        "by_type": by_type,
        "today": today_count,
        "last_7_days": week_count,
        "daily": daily_data,
    }), 200


# ---------------------------------------------------------------------------
# GET /api/v1/admin/export — exportação CSV
# ---------------------------------------------------------------------------
@admin_bp.get("/export")
@admin_required
def export_csv():
    # Mesmos filtros da listagem
    status = request.args.get("status")
    lead_type = request.args.get("type")
    bairro = request.args.get("bairro")
    search = request.args.get("search")

    query = Lead.query

    if status:
        try:
            query = query.filter(Lead.status == LeadStatus(status))
        except ValueError:
            return _erro("invalid_status", f"Status inválido: {status}", 400)

    if lead_type:
        try:
            query = query.filter(Lead.type == LeadType(lead_type))
        except ValueError:
            return _erro("invalid_type", f"Tipo inválido: {lead_type}", 400)

    if bairro:
        query = query.filter(Lead.bairro.ilike(f"%{bairro}%"))

    if search:
        search_term = f"%{search}%"
        query = query.filter(
            or_(
                Lead.name.ilike(search_term),
                Lead.email.ilike(search_term),
                Lead.phone.ilike(search_term),
            )
        )

    leads = query.order_by(desc(Lead.created_at)).all()

    # Gera CSV em memória
    output = StringIO()
    writer = csv.writer(output)

    # Cabeçalho - protege contra CSV injection prefixando com '
    header = [
        "id", "type", "name", "email", "phone", "source", "status",
        "bairro", "cesta_tipo", "extra", "converted_at", "created_at"
    ]
    writer.writerow(header)

    for lead in leads:
        row = [
            lead.id,
            lead.type.value if lead.type else "",
            lead.name,
            lead.email or "",
            lead.phone,
            lead.source or "",
            lead.status.value if lead.status else "",
            lead.bairro or "",
            lead.cesta_tipo or "",
            str(lead.extra).replace('"', '""'),  # escape aspas
            lead.converted_at.isoformat() if lead.converted_at else "",
            lead.created_at.isoformat() if lead.created_at else "",
        ]
        # Proteção contra CSV injection: prefixa campos que começam com =, +, -, @
        safe_row = []
        for cell in row:
            cell_str = str(cell)
            if cell_str and cell_str[0] in ("=", "+", "-", "@"):
                cell_str = "'" + cell_str
            safe_row.append(cell_str)
        writer.writerow(safe_row)

    output.seek(0)
    response = make_response(output.getvalue())
    response.headers["Content-Type"] = "text/csv; charset=utf-8"
    response.headers["Content-Disposition"] = f'attachment; filename="leads_{datetime.utcnow().strftime("%Y%m%d_%H%M%S")}.csv"'

    return response
