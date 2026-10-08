from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.db.public_queries import PublicReader, get_public_reader
from app.models.v2 import CoreView, MetricView, Page
from app.models.v2_queries import AnalyticsQuery, CoreQuery
from app.v2.analytics import analytics_page

router = APIRouter(prefix="/v2", tags=["Native top-cut analytics"])
Reader = Annotated[PublicReader, Depends(get_public_reader)]


@router.get("/meta", response_model=Page[MetricView])
def meta(reader: Reader, query: Annotated[AnalyticsQuery, Query()]):
    """One exact population, inclusive UTC window; complete bounded reduction before paging."""
    return analytics_page(query, reader=reader, endpoint="/v2/meta")


@router.get("/cores", response_model=Page[CoreView])
def cores(reader: Reader, query: Annotated[CoreQuery, Query()]):
    """Canonical registered-roster sets; rates exclude mirror sides, not team outcomes."""
    return analytics_page(query, reader=reader, endpoint="/v2/cores")
