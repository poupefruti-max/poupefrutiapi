"""Pacote de serviços."""

from .leads import (
    create_lead,
    update_lead_status,
    convert_lead,
    LeadError,
    LeadAlreadyConvertedError,
    DuplicateLeadError,
)

__all__ = [
    "create_lead",
    "update_lead_status",
    "convert_lead",
    "LeadError",
    "LeadAlreadyConvertedError",
    "DuplicateLeadError",
]
