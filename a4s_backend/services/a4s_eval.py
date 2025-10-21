import uuid
import httpx

from config.settings import EVAL_PREFIX, EVAL_URL

BASE_URL = EVAL_URL + EVAL_PREFIX

async def autodiscover_datashape(datashape_pid: uuid.UUID):
    async with httpx.AsyncClient(base_url=BASE_URL) as client:
        return await client.get(f'datashape/autodiscover/{datashape_pid}')


async def trigger_evaluation_task():
    async with httpx.AsyncClient(base_url=BASE_URL) as client:
        return await client.get(f'evaluate')