from .models import AuditLog


def log(action: str, actor=None, obj=None, ip: str | None = None, **details) -> AuditLog:
    actor = actor if actor is not None and getattr(actor, "is_authenticated", False) else None
    return AuditLog.objects.create(
        actor=actor, action=action, ip_address=ip,
        object_type=obj.__class__.__name__ if obj is not None else "",
        object_id=str(getattr(obj, "pk", "")) if obj is not None else "",
        details={k: str(v) for k, v in details.items()})
