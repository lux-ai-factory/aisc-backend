from ninja import Router, ModelSchema
from ninja.security import django_auth
from ninja_jwt.authentication import JWTAuth
from typing import List
from my_application.models import Item

class ItemInSchema(ModelSchema):
    class Meta:
        model = Item
        fields = ["name"]

class ItemOutSchema(ModelSchema):
    class Meta:
        model = Item
        fields = ["id", "name"]

router = Router(tags=["items"], auth=[JWTAuth(), django_auth])


@router.post("/", response=ItemInSchema)
def create_item(request, data: ItemInSchema):
    user = request.user
    item = Item(name=data.name, owner=user)
    item.save()
    return item

@router.get("/", response=List[ItemOutSchema])
def list_items(request):
    user = request.user
    items = Item.objects.filter(owner=user)
    return items