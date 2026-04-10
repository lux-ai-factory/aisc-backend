from django.test import TestCase

from vera_backend.models import DataShapeStatus, EvaluationStatus
from vera_backend.tests.integration import project_test_client, dataset_test_client, datashape_test_client, \
    model_test_client, evaluation_test_client
from vera_backend.tests.utils import create_feature_in_schema_list, \
    create_measure_in_schema_list_for_datashape, create_measure_in_schema_list_for_model_eval

project_name = 'test'

day_int = 90
batch_count = 10
project_frequency = f'{day_int}d'
project_window_size = f'{day_int} days'
training_dataset_name = 'training-dataset'
testing_dataset_name = 'testing-dataset'
model_name = 'model'

class IntegrationTestCase(TestCase):


    async def test_integration(self):
        # Create Project
        project = await project_test_client.create_project(project_name)
        self.assertIsNotNone(project)
        self.assertEqual(project.name, project_name)


        # Get projects list
        projects = await project_test_client.get_projects()
        self.assertIsNotNone(projects)
        self.assertEqual(len(projects), 1)


        # Update project with frequency and window size
        project = projects[0]
        project = await project_test_client.patch_project(project.pid, project.name, project_frequency, project_window_size)
        self.assertIsNotNone(project)
        self.assertEqual(project.name, project_name)
        self.assertEqual(project.frequency, project_frequency)
        self.assertEqual(project.window_size, project_window_size)


        # Create training dataset
        training_dataset = await project_test_client.create_project_dataset(project.pid, training_dataset_name)
        self.assertIsNotNone(training_dataset)
        self.assertEqual(training_dataset.name, training_dataset_name)


        # Create testing dataset
        testing_dataset = await project_test_client.create_project_dataset(project.pid, testing_dataset_name)
        self.assertIsNotNone(testing_dataset)
        self.assertEqual(testing_dataset.name, testing_dataset_name)


        # Create model
        model = await project_test_client.create_project_model(project.pid, model_name, training_dataset.pid)
        self.assertIsNotNone(model)
        self.assertEqual(model.name, model_name)
        self.assertEqual(model.dataset['pid'], training_dataset.pid)
        self.assertEqual(model.dataset['name'], training_dataset_name)


        # Upload file for training data and trigger eval feature auto discovery
        # (a4s-web ) PUT    /api/v1/datasets/{dataset.pid}/data                 -   Response body   : {"file_name": "2c90e470-1fe2-458a-9a2f-a93e2a3bef80.parquet"}
        upload_dataset_file_result = await dataset_test_client.upload_dataset_file(training_dataset.pid)
        self.assertIsNotNone(upload_dataset_file_result.file_name)
        self.assertIsNotNone(upload_dataset_file_result.datashape_pid)


        # a4s-eval get datashape to auto discover features
        # (a4s-eval) GET    /api/v1/datashapes/{datashape_pid}                    -   Response body   : {"features": [], "dataset": {"pid": "9ad203ca-ed5a-4254-9d58-c1620262dc26", "name": "training dataset", "data": "2c90e470-1fe2-458a-9a2f-a93e2a3bef80.parquet"}, "dataset_pid": "9ad203ca-ed5a-4254-9d58-c1620262dc26", "date": {"name": "", "pid": ""}, "target": {"name": "", "pid": ""}, "id": 1, "pid": "0c2514cf-fba6-42e8-bf8e-c9be88807f5e", "status": "Manual", "date_feature": null, "target_feature": null}
        datashape = await datashape_test_client.get_datashape(upload_dataset_file_result.datashape_pid)
        self.assertIsNotNone(datashape)


        # a4s-eval patch features to datashape
        # (a4s-eval) PATCH  /api/v1/datasets/{dataset.pid}/datashape            -   Request body    : {"features": [{"pid": "d5e79ea4-aa32-46f0-9f85-3a1d3e18fc44", "name": "loan_amnt", "feature_type": "Float", "min_value": 1000.0, "max_value": 35000.0}, {"pid": "82de1780-9c16-41ec-b178-79574d6cf735", "name": "term", "feature_type": "Integer", "min_value": 36.0, "max_value": 60.0}, {"pid": "bd260d25-c9e6-4da6-919a-6d2f0f4ace5d", "name": "int_rate", "feature_type": "Float", "min_value": 6.03, "max_value": 24.2}, {"pid": "8b99154b-baed-4c48-8761-29965dab4e63", "name": "installment", "feature_type": "Float", "min_value": 31.3, "max_value": 1350.84}, {"pid": "3239fe47-7f39-4282-b884-1b0f3337ce6a", "name": "sub_grade", "feature_type": "Integer", "min_value": 0.0, "max_value": 30.0}, {"pid": "3118d1d1-e3fb-4268-be82-eea382d483fe", "name": "emp_length", "feature_type": "Integer", "min_value": 0.0, "max_value": 10.0}, {"pid": "cca00249-c924-437a-8494-85f1b4f22a7d", "name": "home_ownership", "feature_type": "Integer", "min_value": 0.0, "max_value": 2.0}, {"pid": "094d7b5c-b133-4dd3-9520-a651e14cd6c2", "name": "annual_inc", "feature_type": "Float", "min_value": 10200.0, "max_value": 650000.0}, {"pid": "a875e99f-45eb-4cf3-bba8-42b156590c04", "name": "verification_status", "feature_type": "Integer", "min_value": 0.0, "max_value": 2.0}, {"pid": "55684511-2de5-48d6-a53a-4c297c3e73df", "name": "issue_d", "feature_type": "Date", "min_value": 0, "max_value": 0}, {"pid": "fe7063e1-bc4e-402f-b904-3df3477a301b", "name": "purpose", "feature_type": "Integer", "min_value": 0.0, "max_value": 12.0}, {"pid": "58ba9683-5883-4050-8937-ed27a088d101", "name": "dti", "feature_type": "Float", "min_value": 0.02, "max_value": 29.74}, {"pid": "4e3fc126-76ba-4419-8888-6a916be33b04", "name": "open_acc", "feature_type": "Integer", "min_value": 2.0, "max_value": 25.0}, {"pid": "7a336b5a-d44f-4b94-9383-7d68f81d8228", "name": "pub_rec", "feature_type": "Float", "min_value": 0.0, "max_value": 1.0}, {"pid": "82004285-d81c-41a2-b8a4-2014a4dbaa25", "name": "revol_bal", "feature_type": "Float", "min_value": 0.0, "max_value": 119011.0}, {"pid": "6203bee9-294b-43ff-8c0c-c67569d2c43d", "name": "revol_util", "feature_type": "Float", "min_value": 0.0, "max_value": 97.2}, {"pid": "c4af199b-90f0-4315-a614-ccf9cf4a5309", "name": "total_acc", "feature_type": "Integer", "min_value": 3.0, "max_value": 62.0}, {"pid": "70a290cb-df32-40dc-81b6-e4a862abecac", "name": "initial_list_status", "feature_type": "Integer", "min_value": 1.0, "max_value": 1.0}, {"pid": "b03da744-0a40-4f65-ba56-aaa415b8b71a", "name": "application_type", "feature_type": "Integer", "min_value": 0.0, "max_value": 0.0}, {"pid": "4c037eb8-5901-4f06-b572-9c83f12bc7c3", "name": "mort_acc", "feature_type": "Integer", "min_value": 0.0, "max_value": 22.0}, {"pid": "ca75fb33-a235-4f8f-8296-8adf909ce915", "name": "pub_rec_bankruptcies", "feature_type": "Integer", "min_value": 0.0, "max_value": 1.0}, {"pid": "e14043f2-b521-4b00-9c3b-26bfae758ba7", "name": "fico_score", "feature_type": "Float", "min_value": 662.0, "max_value": 812.0}, {"pid": "26d13735-4402-45e6-ad70-024b40860476", "name": "month_of_year", "feature_type": "Integer", "min_value": 3.0, "max_value": 3.0}, {"pid": "5f6ce100-d21f-49e9-ae1b-7a91dade55a0", "name": "ratio_loan_amnt_annual_inc", "feature_type": "Float", "min_value": 0.025, "max_value": 0.5}, {"pid": "282e72ad-3c23-407c-8f44-83d27e36e68e", "name": "ratio_open_acc_total_acc", "feature_type": "Float", "min_value": 0.1304347826086956, "max_value": 1.0}, {"pid": "9e03f443-d2d1-466e-975f-d8dc566874e0", "name": "month_since_earliest_cr_line", "feature_type": "Integer", "min_value": 49.0, "max_value": 510.0}, {"pid": "c0c63e6b-b31e-4541-9f7d-522287d019fa", "name": "ratio_pub_rec_month_since_earliest_cr_line", "feature_type": "Float", "min_value": 0.0, "max_value": 0.0099009900990099}, {"pid": "16fe7f40-6f4e-4364-a073-976968a9c25b", "name": "ratio_pub_rec_bankruptcies_month_since_earliest_cr_line", "feature_type": "Float", "min_value": 0.0, "max_value": 0.0099009900990099}, {"pid": "2a6eb716-488b-422b-90a7-c818d9cb3cac", "name": "ratio_pub_rec_bankruptcies_pub_rec", "feature_type": "Float", "min_value": -1.0, "max_value": 1.0}, {"pid": "d659e543-869c-4153-9795-fa502298b76b", "name": "charged_off", "feature_type": "Integer", "min_value": 0.0, "max_value": 1.0}], "target": null, "date": null}	-	Response body: {"features": [{"id": 31, "pid": "1c3a0792-72b6-49f0-9f05-edcbae1fce2b", "name": "loan_amnt", "description": "", "feature_type": "Float", "min_value": 1000.0, "max_value": 35000.0}, {"id": 32, "pid": "33ccc924-71cb-48b4-a06b-ab9e5583da19", "name": "term", "description": "", "feature_type": "Integer", "min_value": 36.0, "max_value": 60.0}, {"id": 33, "pid": "45c49fb2-62d9-4753-a548-a8646300d411", "name": "int_rate", "description": "", "feature_type": "Float", "min_value": 6.03, "max_value": 24.2}, {"id": 34, "pid": "ee5d5f60-8ead-4719-b560-3d5a8c4eca99", "name": "installment", "description": "", "feature_type": "Float", "min_value": 31.3, "max_value": 1350.84}, {"id": 35, "pid": "9963e9b6-5890-4944-a0ec-94aa3e9c7c43", "name": "sub_grade", "description": "", "feature_type": "Integer", "min_value": 0.0, "max_value": 30.0}, {"id": 36, "pid": "b98d8ff0-3c26-46b4-9192-935fd09c0e51", "name": "emp_length", "description": "", "feature_type": "Integer", "min_value": 0.0, "max_value": 10.0}, {"id": 37, "pid": "64d14df3-9313-4b2c-9f33-85a46f814d82", "name": "home_ownership", "description": "", "feature_type": "Integer", "min_value": 0.0, "max_value": 2.0}, {"id": 38, "pid": "949115e3-45b0-46a1-a0f3-12cbf123978f", "name": "annual_inc", "description": "", "feature_type": "Float", "min_value": 10200.0, "max_value": 650000.0}, {"id": 39, "pid": "bd865497-1800-4958-96d6-e279b1d22ef4", "name": "verification_status", "description": "", "feature_type": "Integer", "min_value": 0.0, "max_value": 2.0}, {"id": 40, "pid": "470a497d-a9a8-484d-81d3-1df059f39478", "name": "issue_d", "description": "", "feature_type": "Date", "min_value": 0.0, "max_value": 0.0}, {"id": 41, "pid": "5878756c-9253-485e-8447-b7a3ff75b720", "name": "purpose", "description": "", "feature_type": "Integer", "min_value": 0.0, "max_value": 12.0}, {"id": 42, "pid": "f6df766d-33f6-4c9d-b284-c4498466237e", "name": "dti", "description": "", "feature_type": "Float", "min_value": 0.02, "max_value": 29.74}, {"id": 43, "pid": "c12f5140-8d65-4bf0-b896-d61e70c1b5d2", "name": "open_acc", "description": "", "feature_type": "Integer", "min_value": 2.0, "max_value": 25.0}, {"id": 44, "pid": "ce44137f-eb21-46a0-a215-f7e12a2a2721", "name": "pub_rec", "description": "", "feature_type": "Float", "min_value": 0.0, "max_value": 1.0}, {"id": 45, "pid": "bdc7f5b9-5f89-491a-9c56-44711de57804", "name": "revol_bal", "description": "", "feature_type": "Float", "min_value": 0.0, "max_value": 119011.0}, {"id": 46, "pid": "fc2e0834-4354-4509-ad24-7d4b09b4bd59", "name": "revol_util", "description": "", "feature_type": "Float", "min_value": 0.0, "max_value": 97.2}, {"id": 47, "pid": "a5a135b2-9a55-49ba-b6af-77f4180e2c5a", "name": "total_acc", "description": "", "feature_type": "Integer", "min_value": 3.0, "max_value": 62.0}, {"id": 48, "pid": "dba3a1ba-a19c-44de-b89a-f2e20fb6b545", "name": "initial_list_status", "description": "", "feature_type": "Integer", "min_value": 1.0, "max_value": 1.0}, {"id": 49, "pid": "ed3b85f5-2a27-4218-be09-314b0ac7a27e", "name": "application_type", "description": "", "feature_type": "Integer", "min_value": 0.0, "max_value": 0.0}, {"id": 50, "pid": "4f26ea14-07d3-4a4c-9e16-a5cdde0a6993", "name": "mort_acc", "description": "", "feature_type": "Integer", "min_value": 0.0, "max_value": 22.0}, {"id": 51, "pid": "b0bb5e4a-2326-421f-ae52-48a0b84771e4", "name": "pub_rec_bankruptcies", "description": "", "feature_type": "Integer", "min_value": 0.0, "max_value": 1.0}, {"id": 52, "pid": "9d8adb99-9879-44a5-970d-ba5859ddfb66", "name": "fico_score", "description": "", "feature_type": "Float", "min_value": 662.0, "max_value": 812.0}, {"id": 53, "pid": "d723f410-67c7-4c53-b046-552d0c43a29c", "name": "month_of_year", "description": "", "feature_type": "Integer", "min_value": 3.0, "max_value": 3.0}, {"id": 54, "pid": "27f9604b-fc7f-4327-819c-b1b6a8a39d31", "name": "ratio_loan_amnt_annual_inc", "description": "", "feature_type": "Float", "min_value": 0.025, "max_value": 0.5}, {"id": 55, "pid": "b3952db2-7bdd-4e2a-b300-e541fcb9a264", "name": "ratio_open_acc_total_acc", "description": "", "feature_type": "Float", "min_value": 0.1304347826086956, "max_value": 1.0}, {"id": 56, "pid": "3e8d9cf8-745c-433d-a2fa-15efe68b992d", "name": "month_since_earliest_cr_line", "description": "", "feature_type": "Integer", "min_value": 49.0, "max_value": 510.0}, {"id": 57, "pid": "da19e15d-bff7-41c1-8623-52599c44c493", "name": "ratio_pub_rec_month_since_earliest_cr_line", "description": "", "feature_type": "Float", "min_value": 0.0, "max_value": 0.0099009900990099}, {"id": 58, "pid": "b58494ea-2a18-4f9a-81b6-ba21ec62fbbd", "name": "ratio_pub_rec_bankruptcies_month_since_earliest_cr_line", "description": "", "feature_type": "Float", "min_value": 0.0, "max_value": 0.0099009900990099}, {"id": 59, "pid": "9c1f2fef-a92b-4b2f-8c40-61ed689ce9c7", "name": "ratio_pub_rec_bankruptcies_pub_rec", "description": "", "feature_type": "Float", "min_value": -1.0, "max_value": 1.0}, {"id": 60, "pid": "8b973a20-2119-4c44-b00d-6200c81665f6", "name": "charged_off", "description": "", "feature_type": "Integer", "min_value": 0.0, "max_value": 1.0}], "dataset": {"pid": "6438ab12-7352-42f5-8c31-1a902443e87e", "name": "testing dataset", "data": "33956a2b-dda3-491d-89db-3d8575c55b6c.parquet"}, "dataset_pid": "6438ab12-7352-42f5-8c31-1a902443e87e", "date": {"name": "", "pid": ""}, "target": {"name": "", "pid": ""}, "id": 2, "pid": "30ce5590-8e31-48c2-9616-b7b9c90cd50c", "status": "Requested", "date_feature": null, "target_feature": null}
        features = create_feature_in_schema_list()
        datashape = await dataset_test_client.patch_dataset_datashape(datashape.dataset['pid'], features)
        self.assertIsNotNone(datashape)


        # a4s-eval update status of datashape to show feature discovery done
        # (a4s-eval) PATCH  /api/v1/datashapes/{dataset.pid}/status?status=Auto -   Response body   : "Auto"
        datashape_status = await datashape_test_client.patch_datashape_status(datashape.pid, DataShapeStatus.Auto)
        self.assertEqual(DataShapeStatus.Auto, datashape_status)


        # Upload model data file
        upload_model_file_result = await model_test_client.upload_model_file(model.pid)
        self.assertIsNotNone(upload_model_file_result.file_name)


        # Clicking import features gets dataset datashape and also assigns it as the expected_datashape to the project
        datashape = await dataset_test_client.get_dataset_datashape(training_dataset.pid)
        self.assertIsNotNone(datashape.pid)


        # Patch features and set date and target features
        # 127.0.0.1:62416 (a4s-web) PATCH /api/v1/projects/1907310a-31b9-4c1a-b168-27dd87a0eeb3/datashape http/1.1 200	-	Request body: {"features":[{"name":"annual_inc","min_value":9600,"max_value":327000,"feature_type":"Float"},{"name":"application_type","min_value":0,"max_value":0,"feature_type":"Integer"},{"name":"dti","min_value":0.11,"max_value":29.52,"feature_type":"Float"},{"name":"emp_length","min_value":0,"max_value":10,"feature_type":"Integer"},{"name":"fico_score","min_value":662,"max_value":817,"feature_type":"Float"},{"name":"home_ownership","min_value":0,"max_value":2,"feature_type":"Integer"},{"name":"initial_list_status","min_value":1,"max_value":1,"feature_type":"Integer"},{"name":"installment","min_value":34.5,"max_value":1318.45,"feature_type":"Float"},{"name":"int_rate","min_value":6.03,"max_value":24.2,"feature_type":"Float"},{"name":"loan_amnt","min_value":1000,"max_value":35000,"feature_type":"Float"},{"name":"month_of_year","min_value":2,"max_value":3,"feature_type":"Integer"},{"name":"month_since_earliest_cr_line","min_value":36,"max_value":476,"feature_type":"Integer"},{"name":"mort_acc","min_value":0,"max_value":19,"feature_type":"Integer"},{"name":"open_acc","min_value":2,"max_value":38,"feature_type":"Integer"},{"name":"pub_rec","min_value":0,"max_value":1,"feature_type":"Float"},{"name":"pub_rec_bankruptcies","min_value":0,"max_value":1,"feature_type":"Integer"},{"name":"purpose","min_value":0,"max_value":12,"feature_type":"Integer"},{"name":"ratio_loan_amnt_annual_inc","min_value":0.0211640211640211,"max_value":0.5,"feature_type":"Float"},{"name":"ratio_open_acc_total_acc","min_value":0.0909090909090909,"max_value":1,"feature_type":"Float"},{"name":"ratio_pub_rec_bankruptcies_month_since_earliest_cr_line","min_value":0,"max_value":0.0098039215686274,"feature_type":"Float"},{"name":"ratio_pub_rec_bankruptcies_pub_rec","min_value":-1,"max_value":1,"feature_type":"Float"},{"name":"ratio_pub_rec_month_since_earliest_cr_line","min_value":0,"max_value":0.0098039215686274,"feature_type":"Float"},{"name":"revol_bal","min_value":0,"max_value":82989,"feature_type":"Float"},{"name":"revol_util","min_value":0,"max_value":97.9,"feature_type":"Float"},{"name":"sub_grade","min_value":0,"max_value":30,"feature_type":"Integer"},{"name":"term","min_value":36,"max_value":60,"feature_type":"Integer"},{"name":"total_acc","min_value":4,"max_value":63,"feature_type":"Integer"},{"name":"verification_status","min_value":0,"max_value":2,"feature_type":"Integer"}],"date":{"name":"issue_d","min_value":0,"max_value":0,"feature_type":"Date"},"target":{"name":"charged_off","min_value":0,"max_value":1,"feature_type":"Integer"}}	-	Response body: {"features": [{"id": 181, "pid": "4a8c77e1-7ce4-44a4-a306-72a2077cb6b3", "name": "annual_inc", "description": "", "feature_type": "Float", "min_value": 9600.0, "max_value": 327000.0}, {"id": 182, "pid": "f7819f90-1203-46b8-818c-ea1c91596f42", "name": "application_type", "description": "", "feature_type": "Integer", "min_value": 0.0, "max_value": 0.0}, {"id": 183, "pid": "a6079959-8afe-4427-8bb5-ff8f62d4d249", "name": "dti", "description": "", "feature_type": "Float", "min_value": 0.11, "max_value": 29.52}, {"id": 184, "pid": "7bfc0569-7193-4839-9976-aac827cd8fb1", "name": "emp_length", "description": "", "feature_type": "Integer", "min_value": 0.0, "max_value": 10.0}, {"id": 185, "pid": "56429881-8819-46e0-b3e8-d6cfec6181f8", "name": "fico_score", "description": "", "feature_type": "Float", "min_value": 662.0, "max_value": 817.0}, {"id": 186, "pid": "765075aa-20e3-4df3-b6ca-5472500764b8", "name": "home_ownership", "description": "", "feature_type": "Integer", "min_value": 0.0, "max_value": 2.0}, {"id": 187, "pid": "1fa5f214-b9a5-4196-bd94-7f3b07511807", "name": "initial_list_status", "description": "", "feature_type": "Integer", "min_value": 1.0, "max_value": 1.0}, {"id": 188, "pid": "48825e08-8757-40cf-8ffc-fb817bdfb467", "name": "installment", "description": "", "feature_type": "Float", "min_value": 34.5, "max_value": 1318.45}, {"id": 189, "pid": "e6151906-cfae-4445-abf1-fd8c74d4c4cf", "name": "int_rate", "description": "", "feature_type": "Float", "min_value": 6.03, "max_value": 24.2}, {"id": 190, "pid": "96d765b2-f7b5-4f2e-8072-0ff1c4c00e73", "name": "loan_amnt", "description": "", "feature_type": "Float", "min_value": 1000.0, "max_value": 35000.0}, {"id": 191, "pid": "fca2312c-c347-44f6-a580-678934858f77", "name": "month_of_year", "description": "", "feature_type": "Integer", "min_value": 2.0, "max_value": 3.0}, {"id": 192, "pid": "68847cb6-2d73-4d48-8d99-82d70bd5393d", "name": "month_since_earliest_cr_line", "description": "", "feature_type": "Integer", "min_value": 36.0, "max_value": 476.0}, {"id": 193, "pid": "609ad521-c8e2-48bf-a571-68dcbfd209f0", "name": "mort_acc", "description": "", "feature_type": "Integer", "min_value": 0.0, "max_value": 19.0}, {"id": 194, "pid": "828642bc-6203-4d0b-be27-cd51a4124b79", "name": "open_acc", "description": "", "feature_type": "Integer", "min_value": 2.0, "max_value": 38.0}, {"id": 195, "pid": "3de963aa-3be7-4ab3-b163-c5f2200b8cf1", "name": "pub_rec", "description": "", "feature_type": "Float", "min_value": 0.0, "max_value": 1.0}, {"id": 196, "pid": "b6d71021-9c53-4900-ae44-1c3c9d03acbe", "name": "pub_rec_bankruptcies", "description": "", "feature_type": "Integer", "min_value": 0.0, "max_value": 1.0}, {"id": 197, "pid": "fa7c1afe-fd55-4fba-842d-067234d5fb3c", "name": "purpose", "description": "", "feature_type": "Integer", "min_value": 0.0, "max_value": 12.0}, {"id": 198, "pid": "38195d08-5b92-413e-88fd-42577cd16985", "name": "ratio_loan_amnt_annual_inc", "description": "", "feature_type": "Float", "min_value": 0.0211640211640211, "max_value": 0.5}, {"id": 199, "pid": "883f2bd5-12f0-4011-a0e6-0cfd0ef61097", "name": "ratio_open_acc_total_acc", "description": "", "feature_type": "Float", "min_value": 0.0909090909090909, "max_value": 1.0}, {"id": 200, "pid": "e5f146de-8f6a-4703-8bff-7f01609a6ae5", "name": "ratio_pub_rec_bankruptcies_month_since_earliest_cr_line", "description": "", "feature_type": "Float", "min_value": 0.0, "max_value": 0.0098039215686274}, {"id": 201, "pid": "822c04df-1f06-4ca9-9901-1a4da5b2e5bd", "name": "ratio_pub_rec_bankruptcies_pub_rec", "description": "", "feature_type": "Float", "min_value": -1.0, "max_value": 1.0}, {"id": 202, "pid": "3b527ac5-d320-4cd9-93c4-7a4e2cf52ba5", "name": "ratio_pub_rec_month_since_earliest_cr_line", "description": "", "feature_type": "Float", "min_value": 0.0, "max_value": 0.0098039215686274}, {"id": 203, "pid": "ddb8b827-acde-4de0-b01d-f0c0a16180d7", "name": "revol_bal", "description": "", "feature_type": "Float", "min_value": 0.0, "max_value": 82989.0}, {"id": 204, "pid": "cbc0915b-d309-43d8-900b-029e9e11f5d9", "name": "revol_util", "description": "", "feature_type": "Float", "min_value": 0.0, "max_value": 97.9}, {"id": 205, "pid": "66e0f1c8-c6a7-4c26-8beb-48a194854e32", "name": "sub_grade", "description": "", "feature_type": "Integer", "min_value": 0.0, "max_value": 30.0}, {"id": 206, "pid": "058b5bfa-bfde-4e2d-8e06-821633ef92f2", "name": "term", "description": "", "feature_type": "Integer", "min_value": 36.0, "max_value": 60.0}, {"id": 207, "pid": "d0cc9a3d-e777-44b9-8cdd-e86ffd28ad58", "name": "total_acc", "description": "", "feature_type": "Integer", "min_value": 4.0, "max_value": 63.0}, {"id": 208, "pid": "42f75d3b-4aee-4ec2-991c-6445b78da5fc", "name": "verification_status", "description": "", "feature_type": "Integer", "min_value": 0.0, "max_value": 2.0}], "dataset": {"pid": "1be41724-a4a0-423e-8ad0-a02f5e0e27e6", "name": "training", "data": "24d83a01-d2a8-4858-9c15-46a60096d85c.parquet"}, "dataset_pid": "1be41724-a4a0-423e-8ad0-a02f5e0e27e6", "date": {"id": 210, "pid": "d38b4359-398b-49c0-a266-0adc3ac1578d", "name": "issue_d", "description": "", "feature_type": "Date", "min_value": 0.0, "max_value": 0.0}, "target": {"id": 209, "pid": "1cc0405e-d040-4e64-be1e-a5328179b30c", "name": "charged_off", "description": "", "feature_type": "Integer", "min_value": 0.0, "max_value": 1.0}, "id": 3, "pid": "2e3557fd-b061-4305-8f50-0cb449cc36e8", "status": "Auto", "date_feature": 210, "target_feature": 209}
        datashape = await project_test_client.patch_project_datashape(project.pid, datashape)
        self.assertIsNotNone(datashape.date)
        self.assertIsNotNone(datashape.target)


        # Get project by name then by id to populate start evaluation form dropdowns
        project = await project_test_client.get_project_by_name(project.name)
        self.assertIsNotNone(project)

        project_details = await project_test_client.get_project_details(project.pid)
        self.assertIsNotNone(project_details)
        self.assertIsNotNone(project_details.expected_datashape)


        # Create evaluation
        evaluation = await evaluation_test_client.create_evaluation(project_details, model_name, training_dataset_name)
        self.assertIsNotNone(evaluation)


        # Send get to eval module to trigger evaluations
        pass

        # a4s-eval gets Pending evaluations
        evaluations = await evaluation_test_client.get_evaluations_by_status(EvaluationStatus.Pending)
        self.assertEqual(len(evaluations), 1)
        self.assertIsNotNone(evaluation)


        # a4s-eval claims pending evaluations and updates status
        evaluation = evaluations[0]
        evaluation_status = await evaluation_test_client.update_evaluation_status(evaluation.evaluation_pid, EvaluationStatus.Processing)
        self.assertEqual(EvaluationStatus.Processing, evaluation_status)


        # a4s-eval gets evaluation details for data drift eval
        evaluation_detail = await evaluation_test_client.get_evaluation_including(evaluation.evaluation_pid, "project,dataset,model,datashape")
        self.assertIsNotNone(evaluation_detail)
        self.assertIsNotNone(evaluation_detail.dataset)
        self.assertIsNotNone(evaluation_detail.model)


        # a4s-eval get dataset file data
        get_dataset_file_data_response = await dataset_test_client.get_dataset_file_data(evaluation_detail.dataset['pid'])
        self.assertIsNotNone(get_dataset_file_data_response)
        self.assertEqual(200, get_dataset_file_data_response.status_code)


        # a4s-eval get model dataset file data
        get_model_dataset_file_data = await dataset_test_client.get_dataset_file_data(evaluation_detail.model['dataset']['pid'])
        self.assertIsNotNone(get_model_dataset_file_data)
        self.assertEqual(200, get_model_dataset_file_data.status_code)


        # a4s-eval gets project datashape, probably unnecessarily as we can return everything in the get evaluation call
        datashape = await project_test_client.get_project_datashape(evaluation_detail.project['pid'])
        self.assertIsNotNone(datashape)
        self.assertIsNotNone(datashape.date)


        # a4s-eval post some dummy measures for the data drift evaluation
        measures = create_measure_in_schema_list_for_datashape(datashape, batch_count, day_int, "wasserstein_distance")
        create_evaluation_measures_response = await evaluation_test_client.create_evaluation_measures(evaluation_detail.pid, measures)
        self.assertIsNotNone(create_evaluation_measures_response)
        self.assertEqual(201, create_evaluation_measures_response.status_code)


        # a4s-eval gets evaluation details for model eval
        evaluation_detail = await evaluation_test_client.get_evaluation_including(evaluation.evaluation_pid,
                                                                                  "project,dataset,model,datashape")
        self.assertIsNotNone(evaluation_detail)
        self.assertIsNotNone(evaluation_detail.dataset)
        self.assertIsNotNone(evaluation_detail.model)


        # a4s-eval get dataset file data
        get_dataset_file_data_response = await dataset_test_client.get_dataset_file_data(
            evaluation_detail.dataset['pid'])
        self.assertIsNotNone(get_dataset_file_data_response)
        self.assertEqual(200, get_dataset_file_data_response.status_code)


        # a4s-eval get model file data
        get_model_file_data = await model_test_client.get_model_file_data(evaluation_detail.model['pid'])
        self.assertIsNotNone(get_model_file_data)
        self.assertEqual(200, get_model_file_data.status_code)


        # a4s-eval gets project datashape, probably unnecessarily as we can return everything in the get evaluation call
        datashape = await project_test_client.get_project_datashape(evaluation_detail.project['pid'])
        self.assertIsNotNone(datashape)
        self.assertIsNotNone(datashape.date)


        # a4s-eval post some dummy measures for the model evaluation
        measures = create_measure_in_schema_list_for_model_eval(batch_count, day_int)
        create_evaluation_measures_response = await evaluation_test_client.create_evaluation_measures(evaluation_detail.pid, measures)
        self.assertIsNotNone(create_evaluation_measures_response)
        self.assertEqual(201, create_evaluation_measures_response.status_code)


        # a4s-eval update evaluation status to show it is done
        evaluation_status = await evaluation_test_client.update_evaluation_status(evaluation.evaluation_pid, EvaluationStatus.Done)
        self.assertEqual(EvaluationStatus.Done, evaluation_status)


        # Get done evaluations
        evaluations = await evaluation_test_client.get_evaluations_by_status(EvaluationStatus.Done)
        self.assertEqual(len(evaluations), 1)


        # Get evaluation measures by name jensenshannon
        evaluation = evaluations[0]
        measures = await evaluation_test_client.get_evaluation_measures(evaluation.evaluation_pid, "jensenshannon")
        self.assertEqual(len(measures), 0)


        # Get evaluation measures by name wasserstein_distance
        measures = await evaluation_test_client.get_evaluation_measures(evaluation.evaluation_pid, "wasserstein_distance")
        self.assertIsNotNone(measures)
        self.assertEqual(len(measures), batch_count * len(datashape.features))
        for measure in measures:
            self.assertIsNotNone(measure.feature)


        # Get evaluation measures by name ROCAUC
        measures = await evaluation_test_client.get_evaluation_measures(evaluation.evaluation_pid, "ROCAUC")
        self.assertIsNotNone(measures)
        self.assertEqual(len(measures), batch_count)
        for measure in measures:
            self.assertIsNone(measure.feature)


        # Get evaluation measures by name MCC
        measures = await evaluation_test_client.get_evaluation_measures(evaluation.evaluation_pid, "MCC")
        self.assertIsNotNone(measures)
        self.assertEqual(len(measures), batch_count)
        for measure in measures:
            self.assertIsNone(measure.feature)


        # Get evaluation measures by name F1
        measures = await evaluation_test_client.get_evaluation_measures(evaluation.evaluation_pid, "F1")
        self.assertIsNotNone(measures)
        self.assertEqual(len(measures), batch_count)
        for measure in measures:
            self.assertIsNone(measure.feature)


        # Get evaluation measures by name Accuracy
        measures = await evaluation_test_client.get_evaluation_measures(evaluation.evaluation_pid, "Accuracy")
        self.assertIsNotNone(measures)
        self.assertEqual(len(measures), batch_count)
        for measure in measures:
            self.assertIsNone(measure.feature)


        # Get evaluation measures by name Precision
        measures = await evaluation_test_client.get_evaluation_measures(evaluation.evaluation_pid, "Precision")
        self.assertIsNotNone(measures)
        self.assertEqual(len(measures), batch_count)
        for measure in measures:
            self.assertIsNone(measure.feature)


        # Get evaluation measures by name Recall
        measures = await evaluation_test_client.get_evaluation_measures(evaluation.evaluation_pid, "Recall")
        self.assertIsNotNone(measures)
        self.assertEqual(len(measures), batch_count)
        for measure in measures:
            self.assertIsNone(measure.feature)

