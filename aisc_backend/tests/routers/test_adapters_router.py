from django.test import TestCase
from ninja.testing import TestAsyncClient

from aisc_backend.routers.adapters import router


client = TestAsyncClient(router)


class AdaptersRouterTestCase(TestCase):
    async def test_the_catalogue_lists_the_builtin_library(self):
        res = await client.get("/adapters")
        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        self.assertEqual(body["abi_version"], "1")
        builtin = body["packages"]["aisc-plugin-interface"]
        entry = next(iter(builtin.values()))
        ids = {a["id"] for a in entry["adapters"]}
        self.assertLessEqual({"csv", "tsv", "parquet", "openai", "rest", "a2a", "oip"}, ids)
        self.assertEqual(entry["source"], "builtin")

    async def test_presets_include_the_standard_forms(self):
        res = await client.get("/presets")
        self.assertEqual(res.status_code, 200, res.text)
        ids = {p["id"] for p in res.json()}
        self.assertLessEqual({"llm", "agent", "model-api", "dataset", "model-file"}, ids)
