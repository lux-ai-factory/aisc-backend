import uuid

from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import HttpResponse
from django.test import TestCase
from ninja.testing import TestAsyncClient
from unittest.mock import patch, MagicMock, AsyncMock

from a4s_backend.models import  DataShapeStatus
from a4s_backend.repositories.project_repository import ProjectRepository
from a4s_backend.routers.project import router as project_router, CreateProjectModelRequest
from a4s_backend.routers.dataset import router as dataset_router
from a4s_backend.routers.datashape import router as datashape_router
from a4s_backend.schemas.dataset import DatasetInSchema, DatasetOutSchema, DataShapeOutSchema
from a4s_backend.schemas.datashape import DataShapeInSchema
from a4s_backend.schemas.model import ModelOutSchema
from a4s_backend.schemas.project import ProjectInSchema, ProjectOutSchema
from a4s_backend.tests.utils import create_feature_in_schema_list

project_repository = ProjectRepository()

project_client = TestAsyncClient(project_router)
dataset_client = TestAsyncClient(dataset_router)
datashape_client = TestAsyncClient(datashape_router)


class IntegrationTestCase(TestCase):


    async def test_integration(self):
        # Create Project
        data = ProjectInSchema(name="test")

        response = await project_client.post('', json=data.dict())
        self.assertEqual(200, response.status_code)
        project = ProjectOutSchema.model_construct(**response.data)
        self.assertIsNotNone(project.pid)


        # Get projects list
        response = await project_client.get('')
        self.assertEqual(200, response.status_code)
        projects = [ProjectOutSchema.model_construct(**item) for item in response.data]
        self.assertEqual(len(projects), 1)
        project = projects[0]


        # Patch project
        data = ProjectInSchema(name=project.name)
        data.frequency = "90d"
        data.window_size = "90 days"

        response = await project_client.patch(f'/{project.pid}', json=data.dict())
        self.assertEqual(200, response.status_code)


        # Create training dataset
        data = DatasetInSchema(name="training")

        response = await project_client.post(f'/{project.pid}/datasets', json=data.dict())
        self.assertEqual(200, response.status_code)
        training_dataset = DatasetOutSchema.model_construct(**response.data)
        self.assertIsNotNone(training_dataset.pid)


        # Create test dataset
        data = DatasetInSchema(name="testing")

        response = await project_client.post(f'/{project.pid}/datasets', json=data.dict())
        self.assertEqual(200, response.status_code)
        testing_dataset = DatasetOutSchema.model_construct(**response.data)
        self.assertIsNotNone(testing_dataset.pid)


        # Create model
        data = CreateProjectModelRequest(name="training model", dataset_pid=training_dataset.pid)

        response = await project_client.post(f'/{project.pid}/models', json=data.dict())
        self.assertEqual(200, response.status_code)
        model = ModelOutSchema.model_construct(**response.data)
        self.assertIsNotNone(model.pid)


        # Upload data to training dataset

        # Mock the file_upload and eval auto_discover calls
        # Upload file for training data and trigger eval feature auto discovery
        # (a4s-web ) PUT    /api/v1/datasets/{dataset.pid}/data                 -   Response body   : {"file_name": "2c90e470-1fe2-458a-9a2f-a93e2a3bef80.parquet"}
        datashape_pid = None
        with (
            patch("a4s_backend.routers.dataset.file_repository.upload_file", new_callable=MagicMock) as mock_upload_response,
            patch("a4s_backend.routers.dataset.autodiscover_datashape", new_callable=AsyncMock) as eval_response,
        ):
            mock_upload_response.return_value = True
            eval_response.return_value = HttpResponse(status=200)

            file = SimpleUploadedFile(
                "test_dataset.csv",
                b"feature1,feature2,target\n1,2,0\n3,4,1\n",
                content_type="text/csv",
            )

            response = await dataset_client.put(
                f'/{training_dataset.pid}/data',
                FILES={"file": file},
            )
            self.assertEqual(200, response.status_code)
            datashape_pid = response.data.get('datashape_pid')
            self.assertIsNotNone(datashape_pid)


        # Get datashape
        # (a4s-eval) GET    /api/v1/datashapes/{datashape_pid}                    -   Response body   : {"features": [], "dataset": {"pid": "9ad203ca-ed5a-4254-9d58-c1620262dc26", "name": "training dataset", "data": "2c90e470-1fe2-458a-9a2f-a93e2a3bef80.parquet"}, "dataset_pid": "9ad203ca-ed5a-4254-9d58-c1620262dc26", "date": {"name": "", "pid": ""}, "target": {"name": "", "pid": ""}, "id": 1, "pid": "0c2514cf-fba6-42e8-bf8e-c9be88807f5e", "status": "Manual", "date_feature": null, "target_feature": null}
        response = await datashape_client.get(f'/{datashape_pid}')
        self.assertEqual(200, response.status_code)
        datashape = DataShapeOutSchema.model_construct(**response.data)
        self.assertIsNotNone(datashape.pid)


        # Eval patch features to datashape
        # (a4s-eval) PATCH  /api/v1/datasets/{dataset.pid}/datashape            -   Request body    : {"features": [{"pid": "d5e79ea4-aa32-46f0-9f85-3a1d3e18fc44", "name": "loan_amnt", "feature_type": "Float", "min_value": 1000.0, "max_value": 35000.0}, {"pid": "82de1780-9c16-41ec-b178-79574d6cf735", "name": "term", "feature_type": "Integer", "min_value": 36.0, "max_value": 60.0}, {"pid": "bd260d25-c9e6-4da6-919a-6d2f0f4ace5d", "name": "int_rate", "feature_type": "Float", "min_value": 6.03, "max_value": 24.2}, {"pid": "8b99154b-baed-4c48-8761-29965dab4e63", "name": "installment", "feature_type": "Float", "min_value": 31.3, "max_value": 1350.84}, {"pid": "3239fe47-7f39-4282-b884-1b0f3337ce6a", "name": "sub_grade", "feature_type": "Integer", "min_value": 0.0, "max_value": 30.0}, {"pid": "3118d1d1-e3fb-4268-be82-eea382d483fe", "name": "emp_length", "feature_type": "Integer", "min_value": 0.0, "max_value": 10.0}, {"pid": "cca00249-c924-437a-8494-85f1b4f22a7d", "name": "home_ownership", "feature_type": "Integer", "min_value": 0.0, "max_value": 2.0}, {"pid": "094d7b5c-b133-4dd3-9520-a651e14cd6c2", "name": "annual_inc", "feature_type": "Float", "min_value": 10200.0, "max_value": 650000.0}, {"pid": "a875e99f-45eb-4cf3-bba8-42b156590c04", "name": "verification_status", "feature_type": "Integer", "min_value": 0.0, "max_value": 2.0}, {"pid": "55684511-2de5-48d6-a53a-4c297c3e73df", "name": "issue_d", "feature_type": "Date", "min_value": 0, "max_value": 0}, {"pid": "fe7063e1-bc4e-402f-b904-3df3477a301b", "name": "purpose", "feature_type": "Integer", "min_value": 0.0, "max_value": 12.0}, {"pid": "58ba9683-5883-4050-8937-ed27a088d101", "name": "dti", "feature_type": "Float", "min_value": 0.02, "max_value": 29.74}, {"pid": "4e3fc126-76ba-4419-8888-6a916be33b04", "name": "open_acc", "feature_type": "Integer", "min_value": 2.0, "max_value": 25.0}, {"pid": "7a336b5a-d44f-4b94-9383-7d68f81d8228", "name": "pub_rec", "feature_type": "Float", "min_value": 0.0, "max_value": 1.0}, {"pid": "82004285-d81c-41a2-b8a4-2014a4dbaa25", "name": "revol_bal", "feature_type": "Float", "min_value": 0.0, "max_value": 119011.0}, {"pid": "6203bee9-294b-43ff-8c0c-c67569d2c43d", "name": "revol_util", "feature_type": "Float", "min_value": 0.0, "max_value": 97.2}, {"pid": "c4af199b-90f0-4315-a614-ccf9cf4a5309", "name": "total_acc", "feature_type": "Integer", "min_value": 3.0, "max_value": 62.0}, {"pid": "70a290cb-df32-40dc-81b6-e4a862abecac", "name": "initial_list_status", "feature_type": "Integer", "min_value": 1.0, "max_value": 1.0}, {"pid": "b03da744-0a40-4f65-ba56-aaa415b8b71a", "name": "application_type", "feature_type": "Integer", "min_value": 0.0, "max_value": 0.0}, {"pid": "4c037eb8-5901-4f06-b572-9c83f12bc7c3", "name": "mort_acc", "feature_type": "Integer", "min_value": 0.0, "max_value": 22.0}, {"pid": "ca75fb33-a235-4f8f-8296-8adf909ce915", "name": "pub_rec_bankruptcies", "feature_type": "Integer", "min_value": 0.0, "max_value": 1.0}, {"pid": "e14043f2-b521-4b00-9c3b-26bfae758ba7", "name": "fico_score", "feature_type": "Float", "min_value": 662.0, "max_value": 812.0}, {"pid": "26d13735-4402-45e6-ad70-024b40860476", "name": "month_of_year", "feature_type": "Integer", "min_value": 3.0, "max_value": 3.0}, {"pid": "5f6ce100-d21f-49e9-ae1b-7a91dade55a0", "name": "ratio_loan_amnt_annual_inc", "feature_type": "Float", "min_value": 0.025, "max_value": 0.5}, {"pid": "282e72ad-3c23-407c-8f44-83d27e36e68e", "name": "ratio_open_acc_total_acc", "feature_type": "Float", "min_value": 0.1304347826086956, "max_value": 1.0}, {"pid": "9e03f443-d2d1-466e-975f-d8dc566874e0", "name": "month_since_earliest_cr_line", "feature_type": "Integer", "min_value": 49.0, "max_value": 510.0}, {"pid": "c0c63e6b-b31e-4541-9f7d-522287d019fa", "name": "ratio_pub_rec_month_since_earliest_cr_line", "feature_type": "Float", "min_value": 0.0, "max_value": 0.0099009900990099}, {"pid": "16fe7f40-6f4e-4364-a073-976968a9c25b", "name": "ratio_pub_rec_bankruptcies_month_since_earliest_cr_line", "feature_type": "Float", "min_value": 0.0, "max_value": 0.0099009900990099}, {"pid": "2a6eb716-488b-422b-90a7-c818d9cb3cac", "name": "ratio_pub_rec_bankruptcies_pub_rec", "feature_type": "Float", "min_value": -1.0, "max_value": 1.0}, {"pid": "d659e543-869c-4153-9795-fa502298b76b", "name": "charged_off", "feature_type": "Integer", "min_value": 0.0, "max_value": 1.0}], "target": null, "date": null}	-	Response body: {"features": [{"id": 31, "pid": "1c3a0792-72b6-49f0-9f05-edcbae1fce2b", "name": "loan_amnt", "description": "", "feature_type": "Float", "min_value": 1000.0, "max_value": 35000.0}, {"id": 32, "pid": "33ccc924-71cb-48b4-a06b-ab9e5583da19", "name": "term", "description": "", "feature_type": "Integer", "min_value": 36.0, "max_value": 60.0}, {"id": 33, "pid": "45c49fb2-62d9-4753-a548-a8646300d411", "name": "int_rate", "description": "", "feature_type": "Float", "min_value": 6.03, "max_value": 24.2}, {"id": 34, "pid": "ee5d5f60-8ead-4719-b560-3d5a8c4eca99", "name": "installment", "description": "", "feature_type": "Float", "min_value": 31.3, "max_value": 1350.84}, {"id": 35, "pid": "9963e9b6-5890-4944-a0ec-94aa3e9c7c43", "name": "sub_grade", "description": "", "feature_type": "Integer", "min_value": 0.0, "max_value": 30.0}, {"id": 36, "pid": "b98d8ff0-3c26-46b4-9192-935fd09c0e51", "name": "emp_length", "description": "", "feature_type": "Integer", "min_value": 0.0, "max_value": 10.0}, {"id": 37, "pid": "64d14df3-9313-4b2c-9f33-85a46f814d82", "name": "home_ownership", "description": "", "feature_type": "Integer", "min_value": 0.0, "max_value": 2.0}, {"id": 38, "pid": "949115e3-45b0-46a1-a0f3-12cbf123978f", "name": "annual_inc", "description": "", "feature_type": "Float", "min_value": 10200.0, "max_value": 650000.0}, {"id": 39, "pid": "bd865497-1800-4958-96d6-e279b1d22ef4", "name": "verification_status", "description": "", "feature_type": "Integer", "min_value": 0.0, "max_value": 2.0}, {"id": 40, "pid": "470a497d-a9a8-484d-81d3-1df059f39478", "name": "issue_d", "description": "", "feature_type": "Date", "min_value": 0.0, "max_value": 0.0}, {"id": 41, "pid": "5878756c-9253-485e-8447-b7a3ff75b720", "name": "purpose", "description": "", "feature_type": "Integer", "min_value": 0.0, "max_value": 12.0}, {"id": 42, "pid": "f6df766d-33f6-4c9d-b284-c4498466237e", "name": "dti", "description": "", "feature_type": "Float", "min_value": 0.02, "max_value": 29.74}, {"id": 43, "pid": "c12f5140-8d65-4bf0-b896-d61e70c1b5d2", "name": "open_acc", "description": "", "feature_type": "Integer", "min_value": 2.0, "max_value": 25.0}, {"id": 44, "pid": "ce44137f-eb21-46a0-a215-f7e12a2a2721", "name": "pub_rec", "description": "", "feature_type": "Float", "min_value": 0.0, "max_value": 1.0}, {"id": 45, "pid": "bdc7f5b9-5f89-491a-9c56-44711de57804", "name": "revol_bal", "description": "", "feature_type": "Float", "min_value": 0.0, "max_value": 119011.0}, {"id": 46, "pid": "fc2e0834-4354-4509-ad24-7d4b09b4bd59", "name": "revol_util", "description": "", "feature_type": "Float", "min_value": 0.0, "max_value": 97.2}, {"id": 47, "pid": "a5a135b2-9a55-49ba-b6af-77f4180e2c5a", "name": "total_acc", "description": "", "feature_type": "Integer", "min_value": 3.0, "max_value": 62.0}, {"id": 48, "pid": "dba3a1ba-a19c-44de-b89a-f2e20fb6b545", "name": "initial_list_status", "description": "", "feature_type": "Integer", "min_value": 1.0, "max_value": 1.0}, {"id": 49, "pid": "ed3b85f5-2a27-4218-be09-314b0ac7a27e", "name": "application_type", "description": "", "feature_type": "Integer", "min_value": 0.0, "max_value": 0.0}, {"id": 50, "pid": "4f26ea14-07d3-4a4c-9e16-a5cdde0a6993", "name": "mort_acc", "description": "", "feature_type": "Integer", "min_value": 0.0, "max_value": 22.0}, {"id": 51, "pid": "b0bb5e4a-2326-421f-ae52-48a0b84771e4", "name": "pub_rec_bankruptcies", "description": "", "feature_type": "Integer", "min_value": 0.0, "max_value": 1.0}, {"id": 52, "pid": "9d8adb99-9879-44a5-970d-ba5859ddfb66", "name": "fico_score", "description": "", "feature_type": "Float", "min_value": 662.0, "max_value": 812.0}, {"id": 53, "pid": "d723f410-67c7-4c53-b046-552d0c43a29c", "name": "month_of_year", "description": "", "feature_type": "Integer", "min_value": 3.0, "max_value": 3.0}, {"id": 54, "pid": "27f9604b-fc7f-4327-819c-b1b6a8a39d31", "name": "ratio_loan_amnt_annual_inc", "description": "", "feature_type": "Float", "min_value": 0.025, "max_value": 0.5}, {"id": 55, "pid": "b3952db2-7bdd-4e2a-b300-e541fcb9a264", "name": "ratio_open_acc_total_acc", "description": "", "feature_type": "Float", "min_value": 0.1304347826086956, "max_value": 1.0}, {"id": 56, "pid": "3e8d9cf8-745c-433d-a2fa-15efe68b992d", "name": "month_since_earliest_cr_line", "description": "", "feature_type": "Integer", "min_value": 49.0, "max_value": 510.0}, {"id": 57, "pid": "da19e15d-bff7-41c1-8623-52599c44c493", "name": "ratio_pub_rec_month_since_earliest_cr_line", "description": "", "feature_type": "Float", "min_value": 0.0, "max_value": 0.0099009900990099}, {"id": 58, "pid": "b58494ea-2a18-4f9a-81b6-ba21ec62fbbd", "name": "ratio_pub_rec_bankruptcies_month_since_earliest_cr_line", "description": "", "feature_type": "Float", "min_value": 0.0, "max_value": 0.0099009900990099}, {"id": 59, "pid": "9c1f2fef-a92b-4b2f-8c40-61ed689ce9c7", "name": "ratio_pub_rec_bankruptcies_pub_rec", "description": "", "feature_type": "Float", "min_value": -1.0, "max_value": 1.0}, {"id": 60, "pid": "8b973a20-2119-4c44-b00d-6200c81665f6", "name": "charged_off", "description": "", "feature_type": "Integer", "min_value": 0.0, "max_value": 1.0}], "dataset": {"pid": "6438ab12-7352-42f5-8c31-1a902443e87e", "name": "testing dataset", "data": "33956a2b-dda3-491d-89db-3d8575c55b6c.parquet"}, "dataset_pid": "6438ab12-7352-42f5-8c31-1a902443e87e", "date": {"name": "", "pid": ""}, "target": {"name": "", "pid": ""}, "id": 2, "pid": "30ce5590-8e31-48c2-9616-b7b9c90cd50c", "status": "Requested", "date_feature": null, "target_feature": null}
        features = create_feature_in_schema_list()
        datashape_in_schema = DataShapeInSchema(features=features, date=None, target=None)

        response = await dataset_client.patch(f'/{training_dataset.pid}/datashape', json=datashape_in_schema.dict())
        self.assertEqual(200, response.status_code)
        datashape = DataShapeOutSchema.model_construct(**response.data)
        self.assertIsNotNone(datashape.pid)

        # Update status of datashape
        # (a4s-eval) PATCH  /api/v1/datashapes/{dataset.pid}/status?status=Auto -   Response body   : "Auto"
        response = await datashape_client.patch(f'/{uuid.UUID(datashape.pid)}/status?status={DataShapeStatus.Auto}')
        self.assertEqual(200, response.status_code)
        self.assertEqual(DataShapeStatus.Auto, response.data)