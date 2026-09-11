from typing import Any

from sqlalchemy import func, select

from app.models import PostMetricSnapshot


def latest_post_metric_snapshot_subquery(post_ids: Any) -> Any:
    return (
        select(PostMetricSnapshot.post_id, func.max(PostMetricSnapshot.id).label("latest_id"))
        .where(PostMetricSnapshot.post_id.in_(post_ids))
        .group_by(PostMetricSnapshot.post_id)
        .subquery()
    )
