from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from app.core.auth import SecurityContext, role_guard
from app.services import sat_catalog

router = APIRouter(prefix='/v1/sat-catalogs', tags=['SAT catalog search'])
read = role_guard('viewer','operator','admin','superadmin')


def execute(action):
    try: return action()
    except sat_catalog.CatalogUnavailable as exc: raise HTTPException(503, str(exc)) from None


@router.get('/status')
def status(ctx: SecurityContext = Depends(read)):
    return execute(sat_catalog.metadata)


@router.get('/search')
def search(kind: Literal['product','unit'], q: str = Query(min_length=1,max_length=200),
           nature: Literal['license','managed_service','implementation','support','goods','other'] = 'other',
           limit: int = Query(default=8,ge=1,le=10), ctx: SecurityContext = Depends(read)):
    return execute(lambda: sat_catalog.search(kind,q,nature,limit))


class Item(BaseModel):
    model_config = ConfigDict(extra='forbid')
    itemNumber: int = Field(gt=0)
    satProductCode: str = Field(pattern=r'^\d{8}$')
    unitCode: str = Field(pattern=r'^[A-Za-z0-9]{2,3}$')


class Validation(BaseModel):
    model_config = ConfigDict(extra='forbid')
    items: list[Item] = Field(min_length=1,max_length=500)
    catalogVersion: str | None = Field(default=None,pattern=r'^[a-f0-9]{64}$')


@router.post('/validate')
def validate(body: Validation, ctx: SecurityContext = Depends(read)):
    return execute(lambda: sat_catalog.validate_pairs([item.model_dump() for item in body.items], body.catalogVersion))
