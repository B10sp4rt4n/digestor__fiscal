"""Offline contract preparation. Not registered as an API or a SAT client."""
from datetime import date, datetime
from hashlib import sha256
import json
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, WithJsonSchema, field_validator, model_validator

RFC = r"^[A-ZÑ&]{3,4}[0-9]{6}[A-Z0-9]{3}$"
SourceDatetime = Annotated[datetime, WithJsonSchema({'type': 'string', 'format': 'source-datetime'})]


class Contract(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True, str_strip_whitespace=True)


class SyncScope(Contract):
    tenant_id: str = Field(min_length=1, max_length=100)
    empresa_id: str = Field(min_length=1, max_length=100)
    empresa_rfc: str = Field(pattern=RFC)
    direction: Literal['emitido', 'recibido']
    period_start: date
    period_end: date

    @model_validator(mode='after')
    def bounded_period(self):
        if not 0 <= (self.period_end - self.period_start).days <= 30:
            raise ValueError('Pilot scope must contain 1 to 31 calendar days')
        return self


class SatMetadata(Contract):
    cfdi_uuid: UUID
    emisor_rfc: str = Field(pattern=RFC)
    receptor_rfc: str = Field(pattern=RFC)
    emisor_nombre: str | None = Field(default=None, max_length=300)
    receptor_nombre: str | None = Field(default=None, max_length=300)
    # Preserve source wall times; do not invent an offset from SAT text.
    fecha_emision: SourceDatetime
    fecha_certificacion: SourceDatetime
    rfc_pac: str = Field(pattern=RFC)
    total: str = Field(pattern=r'^[0-9]{1,18}(\.[0-9]{1,6})?$')
    tipo_comprobante: Literal['I', 'E', 'T', 'N', 'P']
    estatus_fiscal: Literal['vigente', 'cancelado']
    fecha_cancelacion: SourceDatetime | None = None
    observed_at: datetime
    raw_ref: str = Field(min_length=1, max_length=1000)
    raw_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    source: Literal['sat_metadata'] = 'sat_metadata'

    @field_validator('observed_at')
    @classmethod
    def observation_has_zone(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError('Observation requires an explicit timezone')
        return value

    @model_validator(mode='after')
    def cancellation_consistent(self):
        if self.estatus_fiscal == 'vigente' and self.fecha_cancelacion is not None:
            raise ValueError('Cancellation date contradicts vigente')
        return self


class MetadataBatch(Contract):
    schema_version: Literal['0.1.0'] = '0.1.0'
    batch_id: UUID
    scope: SyncScope
    coverage: Literal['unknown', 'partial', 'complete']
    # Local workflow phase, NOT a translation of a SAT numeric status.
    phase: Literal['requested', 'waiting', 'downloaded', 'validated', 'failed', 'expired']
    manifest_ref: str | None = Field(default=None, min_length=1, max_length=1000)
    manifest_sha256: str | None = Field(default=None, pattern=r'^[a-f0-9]{64}$')
    expected_count: int | None = Field(default=None, ge=0, le=1000, strict=True)
    observations: tuple[SatMetadata, ...] = Field(max_length=1000)

    @model_validator(mode='after')
    def consistent_scope(self):
        if self.coverage == 'complete' and self.phase != 'validated':
            raise ValueError('Only validated manifests can declare complete coverage')
        if self.coverage == 'complete' and (not self.manifest_ref or not self.manifest_sha256
                                           or self.expected_count != len(self.observations)):
            raise ValueError('Complete coverage requires a preserved manifest and matching row count')
        identities = set()
        for row in self.observations:
            owner = row.emisor_rfc if self.scope.direction == 'emitido' else row.receptor_rfc
            if owner != self.scope.empresa_rfc:
                raise ValueError('Document RFC does not match explicit company/direction')
            if not self.scope.period_start <= row.fecha_emision.date() <= self.scope.period_end:
                raise ValueError('Document is outside the selected issue-date period')
            if row.cfdi_uuid in identities:
                raise ValueError('Duplicate UUID in batch; resolve conflicting rows before validation')
            identities.add(row.cfdi_uuid)
        return self


class CimaMetadataRecord(SatMetadata):
    document_identity: str = Field(pattern=r'^[a-f0-9]{64}$')
    idempotency_key: str = Field(pattern=r'^[a-f0-9]{64}$')
    direccion: Literal['emitido', 'recibido']
    enrichment_required: Literal[True] = True


class CimaMetadataEnvelope(Contract):
    schema_version: Literal['0.1.0'] = '0.1.0'
    dataset: Literal['sat_metadata_observation'] = 'sat_metadata_observation'
    batch_id: UUID
    scope: SyncScope
    coverage: Literal['unknown', 'partial', 'complete']
    phase: Literal['validated'] = 'validated'
    manifest_ref: str | None
    manifest_sha256: str | None
    expected_count: int | None
    records: tuple[CimaMetadataRecord, ...]


def _key(parts):
    return sha256(json.dumps(parts, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def prepare_cima_batch(batch: MetadataBatch, *, authenticated_tenant: str,
                       authorized_company: tuple[str, str]) -> dict:
    """Pure proposed envelope; caller must resolve company membership server-side.

    No CIMA delivery, database operation, XML enrichment or cash registration.
    """
    scope = batch.scope
    if scope.tenant_id != authenticated_tenant or (scope.empresa_id, scope.empresa_rfc) != authorized_company:
        raise ValueError('Unauthorized tenant/company scope')
    if batch.phase != 'validated':
        raise ValueError('Only validated rows may be prepared for CIMA')
    records = []
    for row in batch.observations:
        identity = _key([scope.tenant_id, scope.empresa_id, scope.empresa_rfc, str(row.cfdi_uuid)])
        observation = row.model_dump(mode='json')
        records.append({
            **observation,
            'document_identity': identity,
            'idempotency_key': _key([identity, scope.direction, observation]),
            'direccion': scope.direction,
            'enrichment_required': True,
        })
    return CimaMetadataEnvelope.model_validate({
        'schema_version': batch.schema_version,
        'dataset': 'sat_metadata_observation',
        'batch_id': str(batch.batch_id),
        'scope': scope.model_dump(mode='json'),
        'coverage': batch.coverage,
        'phase': batch.phase,
        'manifest_ref': batch.manifest_ref,
        'manifest_sha256': batch.manifest_sha256,
        'expected_count': batch.expected_count,
        'records': records,
    }).model_dump(mode='json')
