from ninja import Router

from config.settings import APP_NAME

router = Router(tags=["app"])


@router.get("/app-name", response=str)
async def get_app_name(request):
    return APP_NAME
