from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_admin
from app.schemas.notification import NotificationItem, NotificationListResponse
from app.services.notification_log import list_logs

router = APIRouter(prefix="/api/notifications", tags=["notifications"], dependencies=[Depends(get_current_admin)])


@router.get("", response_model=NotificationListResponse)
async def notifications(
    db: AsyncSession = Depends(get_db),
    limit: int = Query(default=100, ge=1, le=200),
) -> NotificationListResponse:
    rows, total = await list_logs(db, limit)
    return NotificationListResponse(
        items=[
            NotificationItem(
                id=row.id,
                kind=row.kind,
                status=row.status,
                email=row.email,
                owner_tag=row.owner_tag,
                account_name=row.account_name,
                resource_name=row.resource_name,
                subscription_id=row.subscription_id,
                new_api_name=row.new_api_name,
                detail=row.detail,
                error=row.error,
                created_at=row.created_at,
            )
            for row in rows
        ],
        total=total,
    )
